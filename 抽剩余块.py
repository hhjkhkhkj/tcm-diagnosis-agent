# -*- coding: utf-8 -*-
"""
抽书里剩下的三块，入库：
  ① 主症加强 / 推荐加减   —— 每方都有，是「加减层」
  ② 结构符合方剂         —— 这个方 = 哪几个经典方的结构
  ③ 方性分析             —— 方的寒热/补泻属性（书里是图，这里抽文字）

输出：入 中医大脑医案库.db 的三张表
"""
import glob
import json
import os
import re
import sqlite3
from collections import Counter

SRC = r"D:\Download\医案集_OCR"
DB = os.path.join(SRC, "中医大脑医案库.db")

# ① 主症加强 / 推荐加减（全角半角括号都要）
RE_JIA = re.compile(r"[\[【［]\s*(主症加强|推荐加减)\s*[\]】］]\s*([^\n]*)")
RE_YAO = re.compile(r"([\u4e00-\u9fff]{2,6})\s*[:：]?\s*([\d.]+)\s*[gG]")

# ② 结构符合方剂：方名散在「结构符合的方剂…」之后的一段里
RE_JIEGOU = re.compile(r"结构符合的方剂(.{0,300})", re.S)

# ③ 方性分析
RE_FANGXING = re.compile(r"方性分析\s*\n\s*([^\n]{6,160})")

# 已知方名（用来从「结构符合」那句里认方）
方名池 = set()
for line in open(os.path.join(SRC, "_方名表.txt"), encoding="utf-8"):
    if "\t" in line:
        n = line.split("\t")[0].strip()
        for p in re.split(r"合|加", n):
            if 2 <= len(p) <= 8:
                方名池.add(p)
# 补几个经方常用名
方名池 |= {"四逆汤", "苓桂术甘汤", "真武汤", "理中汤", "桂枝汤", "麻黄汤", "小柴胡汤",
           "大柴胡汤", "柴胡桂枝汤", "柴胡加龙骨牡蛎汤", "附子理中汤", "补中益气汤",
           "当归芍药散", "葛根汤", "白虎汤", "承气汤", "五苓散", "吴茱萸汤", "小青龙汤",
           "桂枝加附子汤", "血府逐瘀汤", "香砂六君子汤", "厚朴温中汤", "炙甘草汤"}


import sys

sys.path.insert(0, SRC)
import _建库                                  # 复用 FIX_YAO
FIX_YAO = dict(_建库.FIX_YAO)
FIX_YAO.update({"千姜": "干姜", "于姜": "干姜", "白": "白芍", "积实": "枳实",
                "秦芃": "秦艽", "猪苓": "猪苓", "炮附子": "炮附子"})


def 抽药(s):
    出 = []
    for m in RE_YAO.finditer(s):
        药 = FIX_YAO.get(m.group(1), m.group(1))
        出.append({"药": 药, "量": float(m.group(2))})
    return 出


def main():
    jia, jie, xing = [], [], []
    for fp in sorted(glob.glob(os.path.join(SRC, "*", "p*.txt"))):
        text = open(fp, encoding="utf-8").read()
        出处 = "%s p%s" % (os.path.basename(os.path.dirname(fp))[-6:],
                         re.search(r"p(\d+)\.txt", fp).group(1))

        for m in RE_JIA.finditer(text):
            药 = 抽药(m.group(2))
            if 药:
                jia.append({"类型": m.group(1), "药": 药, "原文": m.group(2).strip()[:90], "出处": 出处})

        for m in RE_JIEGOU.finditer(text):
            句 = m.group(1).strip()
            命 = [n for n in 方名池 if n in 句]
            if 命:
                jie.append({"方": "、".join(sorted(set(命), key=len, reverse=True)[:6]),
                            "原文": 句[:150], "出处": 出处})

        m = RE_FANGXING.search(text)
        if m:
            描述 = m.group(1).strip()
            # 去掉那段套话（"中医大脑可以就…并且列出以下的方性图。"）
            描述 = re.sub(r"中医大脑可以就方剂的单味药药性和比例算出方性[，,、]?\s*"
                          r"并且列出以下的方性图[。.]?", "", 描述)
            描述 = re.sub(r"^[，,、。\s]+", "", 描述).strip()
            描述 = re.sub(r"^中医大脑[^。]*。\s*", "", 描述).strip()   # 去掉开头的套话句
            if len(描述) >= 10 and "方性图" not in 描述[:20]:
                xing.append({"描述": 描述, "出处": 出处})

    con = sqlite3.connect(DB)
    c = con.cursor()
    for 名, 数据, 列 in [("加减", jia, "类型,药,原文,出处"),
                        ("结构符合方", jie, "方,原文,出处"),
                        ("方性", xing, "描述,出处")]:
        c.execute("DROP TABLE IF EXISTS %s" % 名)
    c.execute("CREATE TABLE 加减(id INTEGER PRIMARY KEY, 类型 TEXT, 药 TEXT, 原文 TEXT, 出处 TEXT)")
    c.execute("CREATE TABLE 结构符合方(id INTEGER PRIMARY KEY, 方 TEXT, 原文 TEXT, 出处 TEXT)")
    c.execute("CREATE TABLE 方性(id INTEGER PRIMARY KEY, 描述 TEXT, 出处 TEXT)")

    for r in jia:
        c.execute("INSERT INTO 加减(类型,药,原文,出处) VALUES(?,?,?,?)",
                  (r["类型"], "、".join("%s%gg" % (x["药"], x["量"]) for x in r["药"]), r["原文"], r["出处"]))
    for r in jie:
        c.execute("INSERT INTO 结构符合方(方,原文,出处) VALUES(?,?,?)", (r["方"], r["原文"], r["出处"]))
    for r in xing:
        c.execute("INSERT INTO 方性(描述,出处) VALUES(?,?)", (r["描述"], r["出处"]))
    con.commit()
    con.close()

    with open(os.path.join(SRC, "_剩余块.json"), "w", encoding="utf-8") as f:
        json.dump({"加减": jia, "结构符合方": jie, "方性": xing}, f, ensure_ascii=False, indent=1)

    print("① 主症加强/推荐加减 : %d 条" % len(jia))
    print("② 结构符合方剂     : %d 条" % len(jie))
    print("③ 方性分析         : %d 条\n" % len(xing))

    print("=== ① 加减（前 10）===")
    for r in jia[:10]:
        print("   [%s] %s" % (r["类型"], r["原文"][:70]))

    print("\n=== ② 结构符合方（前 12）===")
    for r in jie[:12]:
        print("   %-32s ← %s" % (r["方"], r["出处"]))

    print("\n=== ③ 方性（前 5）===")
    for r in xing[:5]:
        print("   %s" % r["描述"][:110])

    cc = Counter()
    for r in jie:
        for x in r["方"].split("、"):
            cc[x] += 1
    print("\n=== 被认定为「结构」最多的方（前 15）===")
    for k, v in cc.most_common(15):
        print("   %-22s %d" % (k, v))


if __name__ == "__main__":
    main()
