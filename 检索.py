# -*- coding: utf-8 -*-
"""
检索引擎 —— 输入四诊，输出候选方 + 依据

不靠模型，全靠库里的事实：
    ① 药对匹配   命中的药对规则（263 条）→ 功效 + 适应证
    ② 候选方推导  从命中药对反推含这些药对的方
    ③ 剂量参考   库里这味药的剂量分布
    ④ 相似病案   最接近的旧案，看医生当时开了什么
    ⑤ 六经公式   症状组「全部满足」判定经（T2）——含鉴别行的排除规则
    ⑥ 安全规则   硬性底线（T6），在线调用时由 安全规则文本() 无条件注入提示词

用法（命令行）：
    py 检索.py                         跑库里第 1 条做自检
    py 检索.py 苔白腻 便溏 齿痕 脉弦     直接查

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
    "脉沉": ["脉沉", "沉脉"],
    "脉细": ["脉细", "细脉"],
    "脉弱": ["弱脉"],
    "脉滑": ["滑脉"],
    "畏寒": ["身冷畏寒", "四肢厥冷手脚冰冷", "手脚冰冷"],
    "口干": ["口渴", "口干但不会想喝水"],
    "失眠": ["不易入睡", "半夜会醒过来", "失眠"],
    # —— 六经公式(T2)用到的标签：不铺这些，公式永远差一个词匹配不上 ——
    "恶寒": ["恶寒", "怕冷", "畏寒", "身冷", "恶风", "怕风"],
    "怕冷": ["恶寒", "畏寒", "身冷"],
    "恶风": ["恶寒", "怕风"],
    "不恶寒反恶热": ["恶热", "怕热", "不恶寒", "反恶热"],
    "大汗大渴": ["大汗", "汗多", "大渴", "口渴"],
    "往来寒热": ["忽冷忽热", "寒热往来", "时冷时热"],
    "口苦咽干目眩": ["口苦", "咽干", "目眩", "头晕"],
    "腹满": ["腹胀", "肚子胀", "腹胀满", "脘腹胀满"],
    "腹胀": ["腹满", "腹胀"],
    "自利不渴": ["下利", "腹泻", "便溏", "自利", "拉肚子"],
    "腹泻": ["下利", "腹泻", "拉肚子"],
    "吐食不下": ["呕吐", "食不下", "吃不下", "恶心"],
    "呕吐": ["呕吐", "恶心"],
    "脉浮": ["脉浮", "浮脉"],
    "浮脉": ["脉浮", "浮脉"],
    "沉脉": ["脉沉", "沉脉"],
    "脉微细": ["脉微", "脉细", "微脉", "细脉", "微弱"],
    "但欲寐": ["嗜睡", "想睡", "困倦", "精神萎靡", "没精神"],
    "嗜睡": ["但欲寐", "嗜睡", "想睡"],
    "精神萎靡": ["没精神", "精神不振", "疲乏", "乏力", "困倦"],
    "四肢厥冷": ["手脚冰冷", "四肢冰冷", "手脚凉", "身冷畏寒"],
    "寒热错杂": ["上热下寒", "寒热夹杂"],
    "饥不欲食": ["饿不想吃", "饿但不想吃", "没胃口", "饥不欲食"],
    "气上撞心": ["气上冲", "心悸", "气上撞心"],
    "心悸": ["心悸", "气上冲"],
    "头项强痛": ["项强", "脖子僵", "头痛项强", "头项强痛"],
    "头晕": ["目眩", "头晕"],
}


def 展开(词):
    """把用户说法展开成库里的候选标签

    SYN 双向认：词命中 key，或词正好是某个 value，都把这一簇互相展开。
    只认 key 的话，"脖子僵/没精神/汗多" 这类口语永远进不来。
    """
    out = {词}
    for k, vs in SYN.items():
        if 词 == k or 词 in k or k in 词 or 词 in vs:
            out.add(k)
            out.update(vs)
    return out


def 安全规则文本(只硬性=False, 排除=()):
    """给模型的安全底线（T6）。计划第 ⑧ 条：**无条件注入，不参与打分**。

    与「候选方」反着来 —— 候选方是「可能对」，安全规则是「绝不能错」。

    ⚠️ **2026-10-14 改：默认从「只给硬性」改成「全给」。**
    原来默认只注入标了「硬性=是」的，而全表 6 条里**只有 1 条**标了硬性 ——
    于是「**生附子必须久煎**」「炙甘草监制附子」「中病即止」「瞑眩反应」
    **一条都没进过提示词**。安全规则宁可多说，不该让一个数据字段决定要不要说。

    `排除`：要跳过的规则名。见 `模型判断.安全底线()` 里为什么排「不替患者开方」。
    """
    try:
        con = sqlite3.connect(DB)
        sql = ("SELECT 规则,倪原话,李原话,硬性,出处 FROM 安全规则"
               + (" WHERE 硬性='是'" if 只硬性 else "") + " ORDER BY id")
        行 = [r for r in con.execute(sql).fetchall() if r[0] not in set(排除)]
        con.close()
    except Exception:
        return ""
    if not 行:
        return ""
    段 = ["【安全底线 · 不可违反】"]
    for 规则, 倪, 李, 硬, _ in 行:
        段.append("- %s%s" % (规则, "（硬性）" if 硬 == "是" else ""))
        if 倪:
            段.append("    倪：%s" % str(倪)[:120])
        if 李:
            段.append("    李：%s" % str(李)[:120])
    return "\n".join(段)


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

        # 归一后的因子优先 —— 原始列里带 OCR 变体
        # （「半夜会圆过来」和「半夜会醒过来」会算成两个症状，检索直接漏匹配）。
        # 2026-10-13 接进来：`医案因子` 里 965 案有规范标签。
        规范 = {}
        try:
            for r in self.con.execute(
                    "SELECT 医案id,维度,因子 FROM 医案因子 WHERE 维度 IN ('症状','舌脉')"):
                规范.setdefault(r["医案id"], {"症状": [], "舌脉": []})[r["维度"]].append(r["因子"])
        except Exception:
            规范 = {}

        # 六经：抽出来的那批（带来源）。**原文的才是医案自己说的，推断的要标出来** ——
        # 两者混着展示会让人以为「这案判的就是少阴」，其实可能只是模型猜的。
        六经 = {}
        try:
            for r in self.con.execute(
                    "SELECT 医案id,因子,来源 FROM 医案因子 WHERE 维度='六经'"):
                六经.setdefault(r["医案id"], []).append((r["因子"], r["来源"]))
        except Exception:
            六经 = {}
        self.经表 = 六经

        # 能参与检索的病案 = 有标签的那些。
        # ⚠️ 这里**不能**再按「方名非空」过滤 —— 倪案 951 例的方名本来就是空的，
        #    一过滤就全被挡掉（2026-10-13 实测：可检索病案只剩 50 例，全是问止，
        #    早上抽的 943 例标签在检索这步全白费）。需要方名的是 `候选方()`，在那边自己过滤。
        self.可检索病案 = []
        for m in self.医案:
            try:
                m["_症状"] = json.loads(m["症状"] or "[]")
                m["_舌脉"] = json.loads(m["舌脉"] or "[]")
            except Exception:
                m["_症状"], m["_舌脉"] = [], []
            if m["id"] in 规范:
                m["_症状"] = 规范[m["id"]]["症状"]
                m["_舌脉"] = 规范[m["id"]]["舌脉"]
            if m["_症状"] or m["_舌脉"]:
                self.可检索病案.append(m)
        # 主干层（倪李）：六经公式做集合匹配，安全规则无条件注入
        try:
            self.六经公式 = [dict(r) for r in self.con.execute("SELECT * FROM 六经公式")]
        except Exception:
            self.六经公式 = []
        try:
            self.安全规则 = [dict(r) for r in self.con.execute("SELECT * FROM 安全规则")]
        except Exception:
            self.安全规则 = []

    def 经标注(self, 案id):
        """这条病案当时判的六经 —— **原文的明写、推断的加 (推)**

        分开标是因为两者分量差得远：原文的是医案自己说的，
        推断的是模型根据症状猜的 —— 混着展示会让人以为「这案判的就是少阴」。
        """
        出 = []
        for 值, 源 in self.经表.get(案id, []):
            出.append(值 if 源 == "原文" else 值 + "(推)")
        return 出

    # ---------- ① 药对匹配 ----------
    def _载别名(self):
        """变体 → 规范词。词表在源目录；镜像里没有这个文件就退化成原文匹配（不报错）。

        为什么要它：`体质` 表的「症状」列是**两次 OCR 之前**的原文，
        带变体（「长期整日全身倦息」vs「倦怠」）。不归一就一条都对不上。
        """
        if hasattr(self, "_别名"):
            return self._别名
        表 = {}
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_词表_新.tsv")
        try:
            for i, ln in enumerate(open(p, encoding="utf-8")):
                if i == 0:
                    continue
                f = ln.rstrip("\n").split("\t")
                if len(f) < 4 or not f[0].strip():
                    continue
                头 = f[0].strip()
                表[头] = 头
                for a in f[3].split(" / "):
                    if a.strip():
                        表.setdefault(a.strip(), 头)
        except Exception:
            表 = {}
        self._别名 = 表
        return 表

    def 匹配药对(self, 标签, topn=12):
        得分 = []
        for r in self.药对:
            文 = (r["主治"] or "") + " " + (r["应用"] or "")
            命中 = [t for t in 标签 if t and t in 文]
            if 命中:
                得分.append({**r, "命中": 命中, "n": len(命中)})
        得分.sort(key=lambda x: (-x["n"], x["药对"]))
        return 得分[:topn]

    # ---------- ①b 六经公式（T2 · 倪李主干） ----------
    def 匹配六经公式(self, 标签, topn=6):
        """症状组「全部满足」才算命中 —— 这是集合匹配，不是关键词包含。

        类型='鉴别' 的行（恶寒+脉沉 → 少阴，非太阳）是**排除规则**，
        价值不低于命中的主证行，所以主证在前、鉴别紧随，两条都返回。
        """
        我 = set(标签)
        出 = []
        for r in self.六经公式:
            try:
                组 = json.loads(r["症状组"] or "[]")
            except Exception:
                continue
            if not 组:
                continue
            命中 = [x for x in 组 if x in 我 or any(x in t or t in x for t in 我)]
            if len(命中) == len(组):
                出.append({**r, "命中": 命中, "n": len(组)})
        出.sort(key=lambda x: (x.get("类型") != "主证", -x["n"]))
        return 出[:topn]

    def 查安全规则(self, 只硬性=False):
        return [r for r in self.安全规则 if (not 只硬性 or r.get("硬性") == "是")]

    # ---------- ② 候选方推导 ----------
    def 候选方(self, 命中药对, topn=6):
        """哪些方的处方里，含了命中的药对（至少含其中一味）

        ⚠️ `topn` 默认 6 —— 这是**截断**，不是「只有 6 首方匹配」。
        调用方（面板）又切了 [:5]，等于最多只给 5 首。要看全的就把 topn 调大。
        """
        想用 = [set(re.split(r"[+＋]", r["药对"])) for r in 命中药对]
        权重 = Counter()
        明细 = defaultdict(list)
        for m in self.可检索病案:
            # 候选方靠「方名」聚合，没方名的（倪案 951 例都是）在这里排除
            if not m.get("方名") or "组方" in m["方名"]:
                continue
            药 = {x["药"] for x in self.con.execute(
                "SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],))}
            for pair, want in zip(命中药对, 想用):
                if want and want <= 药:
                    权重[m["方名"]] += pair["n"]
                    明细[m["方名"]].append(pair["药对"])
        return [(f, w, 明细[f]) for f, w in 权重.most_common(topn)]

    # ---------- ⑤ 体质反查（书里的「体质分析」表） ----------
    def 体质反查(self, 标签, topn=8):
        """症状集合 → 体质倾向。同一体质在多则医案里出现过，按累计命中排序。"""
        if not hasattr(self, "_体质表"):
            try:
                self._体质表 = [dict(r) for r in self.con.execute("SELECT * FROM 体质")]
            except Exception:
                self._体质表 = []
        我 = set(标签)
        别名 = self._载别名()
        组 = defaultdict(lambda: {"得分": 0, "共有": set(), "条数": 0})
        for r in self._体质表:
            his = set()
            for x in (r["症状"] or "").split("、"):
                x = x.strip()
                if x:
                    his.add(别名.get(x, x))     # 先归一，否则变体匹配不上
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
        空 = {"加减": [], "方性": [], "结构": []}
        if not pdf or not page:      # 倪案没有 pdf/页码（来源是 .md），没得就近匹配
            return 空
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
        for m in self.可检索病案:
            if 排除id is not None and m["id"] == 排除id:
                continue
            his = set(m["_症状"]) | set(m["_舌脉"])
            if not his:
                continue
            交集 = 我 & his
            if not 交集:
                continue
            加权 = len(交集) + len(舌脉集 & set(m["_舌脉"])) * 1.5
            # 大数据组方 / 倪案（方名本来就是空的），方名位置改成显示实际用药
            名 = m.get("方名") or ""
            if not 名 or "组方" in 名:
                药 = [x["药"] for x in self.con.execute(
                    "SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],))]
                if 药:
                    # ⚠️ 带上「共几味」—— 只显示前 11 味时，读者不知道后面还有多少，
                    #    会以为这张方就这 11 味（2026-10-14 排查静默截断时发现）
                    名 = "（自组方 %d 味：%s%s）" % (
                        len(药), "、".join(药[:11]), "…" if len(药) > 11 else "")
                else:
                    名 = "（自组方）"
            # ⚠️ pdf 只能用**真的 pdf 文件名**（倪案没有，留空）。
            #    不能兜底成「书」——倪案 951 例的「书」全一样，会让下面的去重
            #    把它们当成同一患者的复诊，**几百条并成一条**（2026-10-13 实测）。
            源 = m.get("pdf") or ""
            书 = m.get("书") or ""
            页 = m.get("页") or 0
            out.append({"方名": 名,
                        "篇": m.get("篇") or 书,
                        "页": ("%s p%s" % (源[-6:], 页)) if 源 else 书,
                        "pdf": 源, "page": 页,
                        "加权": round(加权, 1), "共有": sorted(交集),
                        "相似度": round(len(交集) / len(我 | his), 3),
                        "六经": self.经标注(m["id"]),
                        "附属": self.附属(m.get("pdf"), m.get("页"))})
        out.sort(key=lambda x: -x["加权"])

        # 去重：同一 PDF 里页码相近的，是同一位患者的多次复诊，只留评分最高的一条
        # ⚠️ 只对**有真 pdf 的**（问止那批）做 —— 倪案没有 pdf/页码，
        #    硬套这条会把它们全判成「同一患者」，几百条并成一条（2026-10-13 实测）
        出 = []
        for r in out:
            if r["pdf"] and any(x["pdf"] == r["pdf"] and abs(x["page"] - r["page"]) <= 40
                                for x in 出):
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
    相似 = 库.相似病案(症状, 舌脉, 排除id=排除id, topn=8)

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

    print("\n【⑤ 六经公式】集合匹配（症状组全部满足才算命中）")
    公式 = 库.匹配六经公式(标签)
    if not 公式:
        print("   （无整组命中）")
    for r in 公式:
        标 = "【鉴别】" if r.get("类型") == "鉴别" else "【主证】"
        try:
            组 = "、".join(json.loads(r["症状组"] or "[]"))
        except Exception:
            组 = r["症状组"]
        print("   %s %s → %s" % (标, 组, r.get("判定") or ""))
        if r.get("排除"):
            print("        排除：%s" % r["排除"])

    print("\n【⑥ 安全规则】硬性 %d 条 / 共 %d 条（在线调用时无条件注入）"
          % (len(库.查安全规则(只硬性=True)), len(库.查安全规则())))
    for r in 库.查安全规则():
        print("   [%s] %s" % ("硬" if r.get("硬性") == "是" else "软", r["规则"]))

    return 命中, 候选, 相似, 公式


def main():
    if len(sys.argv) > 1:
        报告(sys.argv[1:])
    else:
        库 = 中医库()
        m = 库.可检索病案[0]
        标签 = m["_症状"] + m["_舌脉"]
        print("=" * 66)
        print("自检：用库里第 1 条病案当输入")
        print("=" * 66)
        报告(标签, 库, 真方=m["方名"], 排除id=m["id"])
        库.close()


if __name__ == "__main__":
    main()
