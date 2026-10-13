# -*- coding: utf-8 -*-
"""⑤ 方剂 · 落进 T3 方剂

两源：
  A. SKILL.md §第五部分 经方核心方剂速查（16方，带主治+六经）
  B. li_depth/modules/03_jingfang.md §三 经方剂量表（13方，李可剂量版）
组成一律**不带剂量**（剂量另有 剂量_规范 / 剂量_观测 两表，不混进来）。
幂等：先按 source 删本脚本管的两批。
"""
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
SKILL = r"D:\Download\_tcm_src\TCM-Skill-main\skill\SKILL.md"
JINGFANG = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references\li_depth\modules\03_jingfang.md"


def 去剂量(s):
    """把 '桂枝45g，芍药45g，大枣12枚。以新订剂量治…' 洗成 '桂枝、芍药、大枣'"""
    s = s.split("。")[0]                              # 只取第一句，砍掉后面的说明
    s = re.sub(r"[（(][^）)]*[）)]", "", s)            # 去括号注释
    s = re.sub(r"[0-9０-９]+(\.[0-9]+)?\s*~?\s*[0-9０-９]*\s*"
               r"(g|G|克|公斤|kg|ml|ML|毫升|两|升|斤|枚|个|片|条|支|钱|分|寸|合|束|杯|盏|丸|把)?", "", s)
    s = re.sub(r"(各|共)(?=[，,、]|$)", "", s)
    s = re.sub(r"\s", "", s)
    药 = []
    for p in re.split(r"[，,、]", s):
        p = p.strip("的 ")
        if p and len(p) <= 6:
            药.append(p)
    return "、".join(药)


def 抽速查表():
    """A 源：SKILL.md 经方核心方剂速查表（16 方）"""
    txt = open(SKILL, encoding="utf-8").read()
    m = re.search(r"###\s*经方核心方剂速查\s*\n(.*?)\n###", txt, re.S)
    if not m:
        raise SystemExit("找不到 经方核心方剂速查 表")
    out = []
    for 线 in m.group(1).splitlines():
        线 = 线.strip()
        if not 线.startswith("|") or set(线) <= set("|-: "):
            continue
        格 = [c.strip().strip("*").strip() for c in 线.strip("|").split("|")]
        if len(格) < 4 or 格[0] in ("方剂", ""):
            continue
        方名, 组成, 主治, 六经 = 格[0], 格[1], 格[2], 格[3]
        out.append((方名, None, 去剂量(组成), 主治, 六经,
                    "通用", "SKILL.md §经方核心方剂速查"))
    return out


def 抽李可剂量表():
    """B 源：03_jingfang.md §三，形如  **方名（备注）：**\\n组成行"""
    txt = open(JINGFANG, encoding="utf-8").read()
    m = re.search(r"##\s*三、经方剂量表.*?\n(.*?)\n##\s*四、", txt, re.S)
    if not m:
        raise SystemExit("找不到 §三 经方剂量表")
    段 = m.group(1)
    节 = None
    out = []
    for 线 in 段.splitlines():
        h = re.match(r"^###\s*(3\.\d+\s*.+)$", 线)
        if h:
            节 = h.group(1).strip()
            continue
        b = re.match(r"^\*\*(.+?)\*\*\s*$", 线.strip())
        if b and 节:
            名 = b.group(1).rstrip("：:").strip()
            备注 = None
            mm = re.match(r"^(.+?)（(.+)）$", 名)
            if mm:
                名, 备注 = mm.group(1).strip(), mm.group(2).strip()
            out.append((名, 备注, None, 节))
    # 再抓每首方下面的组成行（**方名：** 的下一非空行）
    行 = 段.splitlines()
    组成 = {}
    for i, 线 in enumerate(行):
        b = re.match(r"^\*\*(.+?)\*\*\s*$", 线.strip())
        if not b:
            continue
        for j in range(i + 1, min(i + 4, len(行))):
            t = 行[j].strip()
            if t and not t.startswith(">") and not t.startswith("**"):
                组成[b.group(1).rstrip("：:").strip()] = t
                break
            if t.startswith("**") or t.startswith("#"):
                break
    recs = []
    for 名, 备注, _, 节 in out:
        key = ("%s（%s）" % (名, 备注)) if 备注 else 名
        原 = 组成.get(key) or 组成.get(名) or ""
        recs.append((名, 备注, 去剂量(原) or None, None, None,
                     "李可", "03_jingfang.md §%s" % 节))
    return recs


def main():
    A = 抽速查表()
    B = 抽李可剂量表()
    # B 里名字跟 A 重的不去重——分源并列，检索时两版都能看到
    数据 = A + B
    con = sqlite3.connect(DB)
    try:
        con.execute("DELETE FROM 方剂 WHERE source IN ('通用','李可')")
        con.executemany(
            "INSERT INTO 方剂(方名,别名,组成,主治,六经,source,出处)"
            " VALUES(?,?,?,?,?,?,?)", 数据)
        con.commit()
        print("  通用(速查表) %d 首 / 李可(剂量表) %d 首 / 合计 %d"
              % (len(A), len(B), len(数据)))
        print("\n抽查·组成是否已去剂量：")
        for 方 in ("桂枝汤", "麻黄汤", "小青龙汤原方", "乌梅丸原方", "真武汤"):
            r = con.execute("SELECT source,组成,主治 FROM 方剂 WHERE 方名=? ORDER BY source",
                            (方,)).fetchall()
            if not r:
                print("  %-12s （无）" % 方)
            for s, z, zz in r:
                print("  %-12s [%s] %s   ｜主治=%s" % (方, s, z, (zz or "—")[:24]))
    finally:
        con.close()


if __name__ == "__main__":
    main()
