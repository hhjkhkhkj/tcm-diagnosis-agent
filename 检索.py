# -*- coding: utf-8 -*-
"""
中医检索引擎 —— 输入四诊，输出候选方 + 依据

不靠模型，全靠库里的事实：
    ① 药对匹配   命中的药对规则（263 条）→ 功效 + 适应证
    ② 候选方推导  从命中药对反推含这些药对的方
    ③ 剂量参考   库里这味药的剂量分布
    ④ 相似病案   最接近的旧案，看医生当时开了什么

用法（命令行）：
    py 中医检索.py                         跑库里第 1 条做自检
    py 中医检索.py 苔白腻 便溏 齿痕 脉弦     直接查

做库用：D:\\Download\\医案集_OCR\\中医大脑医案库.db
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

DB = r"D:\Download\医案集_OCR\中医大脑医案库.db"

# 输入词 -> 库里标签 的常见说法对齐（用户可能说简称）
SYN = {
    "苔白腻": ["舌苔白", "舌苔厚腻", "苔白腻"],
    "苔黄腻": ["舌苔黄", "舌苔厚腻"],
    "齿痕": ["舌有齿痕"],
    "便溏": ["大便溏", "软便"],
    "便干": ["大便干", "大便硬", "便秘"],
    "脉弦": ["弦脉"],
    "脉数": ["数脉"],
    "脉沉": ["沉脉"],
    "脉细": ["细脉"],
    "脉弱": ["弱脉"],
    "脉滑": ["滑脉"],
    "畏寒": ["身冷畏寒", "四肢厥冷手脚冰冷", "手脚冰冷"],
    "口干": ["口渴", "口干但不会想喝水"],
    "失眠": ["不易入睡", "半夜会醒过来", "失眠"],
}


def 展开(词):
    """把用户说法展开成库里的候选标签"""
    out = set()
    for k, vs in SYN.items():
        if 词 == k or 词 in k or k in 词:
            out.update(vs)
    out.add(词)
    return out


class 中医库:
    def __init__(self, path=DB):
        # check_same_thread=False：FastAPI 在线程池里跑接口，连接要跨线程用
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        # 药对表 + 单味药表 一起装进来，统一叫「规则」
        self.药对 = []
        for tbl, 类 in [("药对", "药对"), ("单味药", "单味药")]:
            try:
                for r in self.con.execute("SELECT * FROM %s" % tbl):
                    self.药对.append({"药对": r["名"], "主治": r["主治"], "应用": r["应用"],
                                      "出处": r["出处"], "次数": r["次数"], "类型": 类})
            except Exception:
                pass
        self.剂量 = {r["药"]: dict(r) for r in self.con.execute("SELECT * FROM 剂量分布")}
        self.医案 = [dict(r) for r in self.con.execute("SELECT * FROM 医案")]

    # ---------- ① 药对匹配 ----------
    def 匹配药对(self, 标签, topn=12):
        得分 = []
        for r in self.药对:
            文 = (r["主治"] or "") + " " + (r["应用"] or "")
            命中 = [t for t in 标签 if t and t in 文]
            if 命中:
                得分.append({**r, "命中": 命中, "n": len(命中)})
        得分.sort(key=lambda x: (-x["n"], x["药对"]))
        return 得分[:topn]

    # ---------- ② 候选方推导 ----------
    def 候选方(self, 命中药对):
        """哪些方的处方里，含了命中的药对（至少含其中一味）"""
        想用 = [set(re.split(r"[+＋]", r["药对"])) for r in 命中药对]
        权重 = Counter()
        明细 = defaultdict(list)
        for m in self.医案:
            if "组方" in m["方名"]:
                continue
            药 = {x["药"] for x in self.con.execute(
                "SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],))}
            for pair, want in zip(命中药对, 想用):
                if want and want <= 药:
                    权重[m["方名"]] += pair["n"]
                    明细[m["方名"]].append(pair["药对"])
        return [(f, w, 明细[f]) for f, w in 权重.most_common(6)]

    # ---------- ⑤ 体质反查（书里的「体质分析」表） ----------
    def 体质反查(self, 标签, topn=8):
        """症状集合 → 体质倾向。同一体质在多则医案里出现过，按累计命中排序。"""
        if not hasattr(self, "_体质表"):
            try:
                self._体质表 = [dict(r) for r in self.con.execute("SELECT * FROM 体质")]
            except Exception:
                self._体质表 = []
        我 = set(标签)
        组 = defaultdict(lambda: {"得分": 0, "共有": set(), "条数": 0})
        for r in self._体质表:
            his = set(x for x in (r["症状"] or "").split("、") if x)
            交 = 我 & his
            if not 交:
                continue
            g = 组[r["体质"]]
            g["得分"] += len(交)
            g["共有"] |= 交
            g["条数"] += 1
        出 = [{"体质": k, "得分": v["得分"], "共有": sorted(v["共有"]), "命中条数": v["条数"]}
              for k, v in 组.items()]
        出.sort(key=lambda x: (-x["得分"], -x["命中条数"]))
        return 出[:topn]

    # ---------- ③ 剂量参考 ----------
    def 查剂量(self, 药):
        d = self.剂量.get(药)
        if not d:
            return None
        return "%g ~ %g g（中位 %g，库内 %d 次）" % (d["最小"], d["最大"], d["中位"], d["出现次数"])

    # ---------- ⑥ 病案附属：加减 / 方性 / 结构符合方 ----------
    def _载附属(self):
        if hasattr(self, "_附"):
            return
        self._附 = {"加减": [], "方性": [], "结构": []}
        for t, k in [("加减", "加减"), ("方性", "方性"), ("结构符合方", "结构")]:
            try:
                for r in self.con.execute("SELECT * FROM %s" % t):
                    self._附[k].append(dict(r))
            except Exception:
                pass

    def 附属(self, pdf, page, 半径=6):
        """按 pdf + 页码就近，取这条病案自己的加减/方性/结构符合方"""
        self._载附属()
        stem = pdf[-6:]
        出 = {"加减": [], "方性": [], "结构": []}
        for k, (列, 长) in [("加减", ("药", 60)), ("方性", ("描述", 110)), ("结构", ("方", 40))]:
            for r in self._附[k]:
                出处 = r.get("出处", "")
                if " p" not in 出处:
                    continue
                p, pg = 出处.rsplit(" p", 1)
                try:
                    if p == stem and abs(int(pg) - page) <= 半径:
                        出[k].append(str(r.get(列, ""))[:长])
                except ValueError:
                    continue
        return 出

    # ---------- ④ 相似病案 ----------
    def 相似病案(self, 症状, 舌脉, 排除id=None, topn=3):
        我 = set(症状) | set(舌脉)
        舌脉集 = set(舌脉)
        out = []
        for m in self.医案:
            if 排除id is not None and m["id"] == 排除id:
                continue
            his = set(json.loads(m["症状"])) | set(json.loads(m["舌脉"]))
            if not his:
                continue
            交集 = 我 & his
            if not 交集:
                continue
            加权 = len(交集) + len(舌脉集 & set(json.loads(m["舌脉"]))) * 1.5
            # 大数据组方的案例，方名是占位符，改成显示实际用药
            名 = m["方名"]
            if "组方" in 名:
                药 = [x["药"] for x in self.con.execute(
                    "SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],))]
                if 药:
                    名 = "（自组方：%s%s）" % ("、".join(药[:11]), "…" if len(药) > 11 else "")
                else:
                    名 = "（自组方）"
            out.append({"方名": 名, "篇": m["篇"],
                        "页": "%s p%d" % (m["pdf"][-6:], m["页"]),
                        "pdf": m["pdf"], "page": m["页"],
                        "加权": round(加权, 1), "共有": sorted(交集),
                        "相似度": round(len(交集) / len(我 | his), 3),
                        "附属": self.附属(m["pdf"], m["页"])})
        out.sort(key=lambda x: -x["加权"])

        # 去重：同一 PDF 里页码相近的，是同一位患者的多次复诊，只留评分最高的一条
        出 = []
        for r in out:
            if any(x["pdf"] == r["pdf"] and abs(x["page"] - r["page"]) <= 40 for x in 出):
                continue
            出.append(r)
            if len(出) >= topn:
                break
        return 出

    def close(self):
        self.con.close()


def 报告(标签原始, 库=None, 真方=None, 排除id=None):
    库 = 库 or 中医库()
    标签 = []
    for t in 标签原始:
        标签.extend(展开(t))
    标签 = sorted(set(标签))

    症状 = [t for t in 标签 if not any(k in t for k in "舌脉苔")]
    舌脉 = [t for t in 标签 if any(k in t for k in "舌脉苔")]

    命中 = 库.匹配药对(标签)
    候选 = 库.候选方(命中)
    相似 = 库.相似病案(症状, 舌脉, 排除id=排除id)

    print("输入标签：", "、".join(标签))
    if 真方:
        print("书上真方：", 真方)

    print("\n【① 命中药对】%d 条" % len(命中))
    for r in 命中[:8]:
        print("   %-22s %s" % (r["药对"], (r["主治"] or "")[:36]))
        print("      命中「%s」 │ %s" % ("、".join(r["命中"][:4]), (r["应用"] or "")[:60]))

    print("\n【② 候选方】从命中药对反推")
    for f, w, pairs in 候选:
        print("   %-30s 权重 %2d  ← %s" % (f, w, "、".join(pairs[:4])))

    if 候选:
        print("\n【③ 剂量参考】候选方里的药，库里怎么用的")
        合药 = set()
        for f, _, _ in 候选[:2]:
            for m in 库.医案:
                if m["方名"] == f:
                    for x in 库.con.execute("SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],)):
                        合药.add(x["药"])
        for y in sorted(合药)[:14]:
            s = 库.查剂量(y)
            if s:
                print("   %-8s %s" % (y, s))

    print("\n【④ 相似病案】")
    for s in 相似:
        print("   %-28s %-6s (%s)  加权 %.1f" % (s["方名"], s["篇"], s["页"], s["加权"]))
        print("      共有：%s" % "、".join(s["共有"][:9]))

    return 命中, 候选, 相似


def main():
    if len(sys.argv) > 1:
        报告(sys.argv[1:])
    else:
        库 = 中医库()
        m = 库.医案[0]
        标签 = json.loads(m["症状"]) + json.loads(m["舌脉"])
        print("=" * 66)
        print("自检：用库里第 1 条病案当输入")
        print("=" * 66)
        报告(标签, 库, 真方=m["方名"], 排除id=m["id"])
        库.close()


if __name__ == "__main__":
    main()
