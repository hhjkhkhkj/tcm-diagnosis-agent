# -*- coding: utf-8 -*-
"""
抽「体质分析」表 —— 症状集合 → 体质倾向

本书每则医案都有一段：中医大脑的"学习模块"给出该患者在不同辨证观点下的体质倾向。
格式：体质名（可能独立成行，也可能和第一个症状粘连）+ 症状列表（可能跨行）

输出：_体质表.json / 入库到 体质 表
"""
import glob
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict

SRC = r"D:\Download\医案集_OCR"
DB = os.path.join(SRC, "中医大脑医案库.db")

RE_START = re.compile(r"体质分析")
RE_END = re.compile(r"中医大脑处方|中医大脑主方|处方中的用药分析")
# 体质名的后缀（长优先）
SUF = "不足|不振|不交|不固|两虚|亏虚|虚弱|阻滞|虚|实|盛|固|郁|瘀|滞|亢|病|证|弱|亏"
RE_INLINE = re.compile(r"^([\u4e00-\u9fff]{2,8}?(?:" + SUF + r"))")
RE_PURE = re.compile(r"^[\u4e00-\u9fff]{2,12}$")
RE_症状 = re.compile(r"[，、,]|舌|脉|苔")


def 解析块(块):
    """返回 [(体质名, [症状…]), …]"""
    行 = [l.strip() for l in 块.split("\n") if l.strip()]
    行 = [l for l in 行 if not re.search(r"中医大脑|学习模块|体质倾向|体质特性|体质特点|体质性|参考依据|代表着他|我们把|细看其|不禁令人", l)]
    出 = []
    当前 = None
    for l in 行:
        # 独立成行的体质名
        if RE_PURE.match(l) and not RE_症状.search(l) and len(l) <= 8:
            当前 = {"名": l, "症": []}
            出.append(当前)
            continue
        # 粘连的：体质名 + 症状
        m = RE_INLINE.match(l)
        if m and (len(m.group(1)) <= 6):
            名 = m.group(1)
            余 = l[m.end():].strip()
            当前 = {"名": 名, "症": [余] if 余 else []}
            出.append(当前)
            continue
        # 症状行，挂到当前体质
        if 当前 is not None:
            当前["症"].append(l)
    # 把症状拆成短词
    净 = []
    for g in 出:
        词 = []
        for s in g["症"]:
            for x in re.split(r"[，,、]", s):
                x = x.strip("。 .")
                if 2 <= len(x) <= 14 and not re.match(r"^\d+$", x):
                    词.append(x)
        if 词:
            净.append({"体质": g["名"], "症状": 词})
    return 净


def main():
    全部 = []
    for fp in sorted(glob.glob(os.path.join(SRC, "*", "p*.txt"))):
        text = open(fp, encoding="utf-8").read()
        m = RE_START.search(text)
        if not m:
            continue
        tail = text[m.end():]
        e = RE_END.search(tail)
        块 = tail[:e.start()] if e else tail[:2000]
        for g in 解析块(块):
            g["出处"] = "%s p%s" % (os.path.basename(os.path.dirname(fp))[-6:],
                                  re.search(r"p(\d+)\.txt", fp).group(1))
            全部.append(g)

    with open(os.path.join(SRC, "_体质表.json"), "w", encoding="utf-8") as f:
        json.dump(全部, f, ensure_ascii=False, indent=1)

    con = sqlite3.connect(DB)
    c = con.cursor()
    c.execute("DROP TABLE IF EXISTS 体质")
    c.execute("CREATE TABLE 体质(id INTEGER PRIMARY KEY, 体质 TEXT, 症状 TEXT, 出处 TEXT)")
    for g in 全部:
        c.execute("INSERT INTO 体质(体质,症状,出处) VALUES(?,?,?)",
                  (g["体质"], "、".join(g["症状"]), g["出处"]))
    con.commit()
    con.close()

    print("抽出 %d 条「体质←症状」样本\n" % len(全部))
    名频 = Counter(g["体质"] for g in 全部)
    print("=== 出现最多的体质（前 25）===")
    for k, v in 名频.most_common(25):
        print("  %-14s %2d 次" % (k, v))

    print("\n=== 各体质最常见的症状（前 3 个代表）===")
    症 = defaultdict(Counter)
    for g in 全部:
        for s in g["症状"]:
            症[g["体质"]][s] += 1
    for k, _ in 名频.most_common(8):
        top = "、".join("%s(%d)" % (s, n) for s, n in 症[k].most_common(6))
        print("  %-12s %s" % (k, top))


if __name__ == "__main__":
    main()
