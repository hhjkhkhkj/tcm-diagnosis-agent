# -*- coding: utf-8 -*-
"""
建库：把医案集的 OCR 文本变成 SQLite 知识库

数据源全部来自这本书本身（不引外部标准）：
    四诊 -> 方名 -> 药味+剂量

输出：D:\\Download\\医案集_OCR\\中医大脑医案库.db
"""
import glob
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict

SRC = r"D:\Download\医案集_OCR"
DB = os.path.join(SRC, "中医大脑医案库.db")

PIAN = ["疑难篇", "情志篇", "脏腑篇", "妇儿篇", "皮肤篇", "痹证篇"]

RE_HEAD = re.compile(r"辨[证辩]论治")
RE_FANG = re.compile(r"[\[【]\s*方名\s*[\]】]\s*(.+)")
RE_YAO = re.compile(r"([一-鿿]{2,6})\s*[:：]?\s*([\d.]+)\s*[gG]")
RE_PIAN = re.compile(r"(疑难篇|情志篇|脏腑篇|妇儿篇|皮肤篇|痹证篇)")
RE_SHI = re.compile(r"^[\u4e00-\u9fa5]{2,8}(脉|苔|舌)$|^(舌|脉)")

FIX_LABEL = {
    "大便贴": "大便溏", "软便便滤": "软便", "软便便糖": "软便",
    "舌有齿疯": "舌有齿痕", "舌有齿症": "舌有齿痕", "活有齿痕": "舌有齿痕",
    "随眠品质差": "睡眠品质差", "半夜会过来": "半夜会醒过来",
    "面色货": "面色黄", "面色膏": "面色黄", "舌呼大": "舌胖大",
    "视线模蝴": "视线模糊", "食欲不摄": "食欲不振", "阳菱": "阳痿",
    "舌湿 苔水滑": "舌湿苔水滑", "长期整日全身倦念": "长期整日全身倦怠",
}
DROP_LABEL = {"辨证论治", "可能疾病", "的面", "的基础", "面色", "舌苔"}

FIX_FANG = {
    "荣胡桂枝汤": "柴胡桂枝汤", "柴胡桂枝干菱汤": "柴胡桂枝干姜汤",
    "乐胡加龙舒牡蛎汤": "柴胡加龙骨牡蛎汤", "棠胡加龙骨牡蛎汤": "柴胡加龙骨牡蛎汤",
    "架胡加龙骨牡蛎汤": "柴胡加龙骨牡蛎汤", "魔贯附子细辛汤": "麻黄附子细辛汤",
    "血府趣痰汤": "血府逐瘀汤", "厚朴渴中汤": "厚朴温中汤",
    "小荣胡汤": "小柴胡汤", "大豌胡汤": "大柴胡汤", "大象胡汤": "大柴胡汤",
    "小案胡汤加桔梗石膏": "小柴胡汤加桔梗石膏", "当归芍药澈": "当归芍药散",
    "挂枝汤": "桂枝汤", "往枝加附子汤": "桂枝加附子汤", "小骨龙汤": "小青龙汤",
    "十味败寒加连翘意仁": "十味败毒散加连翘薏苡仁", "竭痹汤": "蠲痹汤",
}


# 药名 OCR 错字 -> 正名
FIX_YAO = {
    "荣胡": "柴胡", "聚胡": "柴胡", "黎胡": "柴胡", "敦胡": "柴胡",
    "婺半夏": "姜半夏", "婴半夏": "姜半夏",
    "酒白药": "酒白芍", "酒白": "酒白芍", "酒白书": "酒白芍",
    "酒白茗": "酒白芍", "酒白为": "酒白芍",
    "安甘草": "炙甘草", "灵甘草": "炙甘草", "灸甘草": "炙甘草",
    "曼甘草": "炙甘草", "奥甘草": "炙甘草", "免甘章": "炙甘草",
    "赤药": "赤芍", "积实": "枳实", "权实": "枳实", "白药": "白芍",
    "干要": "干姜", "千菱": "干姜", "干菱": "干姜", "干鞭": "干姜",
    "干蔓": "干姜", "干婴": "干姜", "千婴": "干姜",
    "桂校": "桂枝",
    "大盛": "大枣", "大麦": "大枣", "大粤": "大枣", "大季": "大枣", "大泰": "大枣",
    "茯荟": "茯苓", "茯茶": "茯苓", "茯书": "茯苓", "茯登": "茯苓", "茯蓉": "茯苓",
    "黄梦": "黄芩", "黄苓": "黄芩",
    "川莺": "川芎", "川萌": "川芎", "川茗": "川芎", "黄芎": "川芎",
    "吴茱英": "吴茱萸", "麻置": "麻黄",
    "天花阶盼": "天花粉", "拮视": "桔梗",
    "生篓": "生姜", "生蔓": "生姜", "生婴": "生姜",   # 生婴+牛夏=生姜+半夏
    "白荷": "薄荷", "荆齐": "荆芥", "白芮": "白芍",
}
# ⚠️ 以下待守拙复核（分不清是错字还是真药）
UNCERTAIN_YAO = {"生费", "货步", "生酸", "黄明胶", "大麦"}


def norm_yao(s):
    return FIX_YAO.get(s, s)


def norm_fang(name):
    name = re.sub(r"[\[\]【】\s]", "", name)
    name = re.sub(r"[，。、；].*$", "", name)
    if "组方" in name:
        return None
    parts = [FIX_FANG.get(p.strip(), p.strip()) for p in name.split("合")]
    parts = [p for p in parts if p]
    return "合".join(sorted(parts)) if parts else None


def norm_label(s):
    s = FIX_LABEL.get(s, s)
    return None if s in DROP_LABEL else s


def main():
    rows = []
    cur_pian = ""
    for fp in sorted(glob.glob(os.path.join(SRC, "*", "p*.txt"))):
        pdf = os.path.basename(os.path.dirname(fp))
        page = int(re.search(r"p(\d+)\.txt", fp).group(1))
        text = open(fp, encoding="utf-8").read()

        # 篇章：扉页只有篇名；或者页眉里带篇名（如「疑难篇【医案1】…」）
        mp = RE_PIAN.search(text[:200])
        if mp:
            cur_pian = mp.group(1)

        m = RE_HEAD.search(text)
        if not m:
            continue
        tail = text[m.end():]
        mf = RE_FANG.search(tail)
        if not mf:
            continue
        raw_fang = re.sub(r"[\[\]【】]", "", mf.group(1)).strip()
        fang = norm_fang(raw_fang) or "（大数据组方）"

        block = tail[:mf.start()]
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        lines = [l for l in lines if "中医大脑" not in l and "问止" not in l]
        if len(lines) < 3:
            continue
        舌脉 = [l for l in lines if RE_SHI.match(l)]
        # 症状是短词；带句读的长句是正文，不要
        症状 = [l for l in lines
                if l not in 舌脉 and 2 <= len(l) <= 12
                and not re.search(r"[。，、；：！？]", l)]

        药 = []
        for l in tail[mf.end():].split("\n")[:40]:
            l = l.strip()
            if 药 and len(药) >= 25:
                break
            if not re.search(r"[\d.]+\s*[gG]", l):
                if 药 and len(药) >= 3:
                    break
                continue
            for mm in RE_YAO.finditer(l):
                药.append((norm_yao(mm.group(1).strip()), float(mm.group(2))))

        rows.append({"篇": cur_pian, "pdf": pdf, "页": page, "症状": 症状,
                     "舌脉": 舌脉, "方名": fang, "原方名": raw_fang, "药": 药})

    # ---- 建库 ----
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    c = con.cursor()
    c.executescript("""
    CREATE TABLE 医案(
      id INTEGER PRIMARY KEY, 篇 TEXT, pdf TEXT, 页 INTEGER,
      症状 TEXT, 舌脉 TEXT, 方名 TEXT, 原方名 TEXT);
    CREATE TABLE 处方(
      id INTEGER PRIMARY KEY, 医案id INTEGER, 药 TEXT, 剂量 REAL);
    CREATE TABLE 词表(
      id INTEGER PRIMARY KEY, 类型 TEXT, 词 TEXT, 频次 INTEGER);
    CREATE TABLE 方名表(
      id INTEGER PRIMARY KEY, 方名 TEXT, 基础方数 INTEGER);
    CREATE TABLE 剂量分布(
      药 TEXT PRIMARY KEY, 出现次数 INTEGER,
      最小 REAL, 中位 REAL, 最大 REAL, 均值 REAL);
    """)

    词频 = Counter()
    方频 = Counter()
    药剂量 = defaultdict(list)

    for r in rows:
        c.execute("INSERT INTO 医案(篇,pdf,页,症状,舌脉,方名,原方名) VALUES(?,?,?,?,?,?,?)",
                  (r["篇"], r["pdf"], r["页"],
                   json.dumps([x for x in (norm_label(s) for s in r["症状"]) if x], ensure_ascii=False),
                   json.dumps([x for x in (norm_label(s) for s in r["舌脉"]) if x], ensure_ascii=False),
                   r["方名"], r["原方名"]))
        cid = c.lastrowid
        方频[r["方名"]] += 1
        for s in r["症状"] + r["舌脉"]:
            s2 = norm_label(s)
            if s2:
                词频[s2] += 1
        for name, dose in r["药"]:
            药剂量[name].append(dose)
            c.execute("INSERT INTO 处方(医案id,药,剂量) VALUES(?,?,?)", (cid, name, dose))

    for w, n in 词频.items():
        类型 = "舌脉" if any(k in w for k in "舌脉苔") else "症状"
        c.execute("INSERT INTO 词表(类型,词,频次) VALUES(?,?,?)", (类型, w, n))
    for f, n in 方频.items():
        c.execute("INSERT INTO 方名表(方名,基础方数) VALUES(?,?)", (f, len(f.split("合"))))
    for name, ds in 药剂量.items():
        ds.sort()
        k = len(ds)
        c.execute("INSERT INTO 剂量分布 VALUES(?,?,?,?,?,?)",
                  (name, k, ds[0], ds[k // 2], ds[-1], round(sum(ds) / k, 1)))

    con.commit()

    print("医案（人次） : %d" % len(rows))
    print("处方条目     : %d" % sum(len(r["药"]) for r in rows))
    print("词表         : %d" % len(词频))
    print("方名         : %d" % len(方频))
    print("有剂量的药   : %d\n" % len(药剂量))

    print("=== 按篇分布 ===")
    pc = Counter(r["篇"] or "(未标)" for r in rows)
    for k, v in pc.most_common():
        print("  %-8s %d 人次" % (k, v))

    print("\n=== 用药频次 Top20 ===")
    cc = Counter()
    for r in rows:
        for n, _ in r["药"]:
            cc[n] += 1
    for k, v in cc.most_common(20):
        ds = sorted(药剂量[k])
        print("  %-8s %2d 次   剂量 %g~%g（中位 %g）" % (k, v, ds[0], ds[-1], ds[len(ds)//2]))

    print("\n=== 方名 Top15 ===")
    for k, v in 方频.most_common(15):
        print("  %-28s %d" % (k, v))

    con.close()
    print("\n库：", DB)


if __name__ == "__main__":
    main()
