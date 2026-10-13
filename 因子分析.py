# -*- coding: utf-8 -*-
"""因子分析 —— 从「一致的因子集合」里找出哪些因素真的影响判断

**这版不预测「该开哪个方」** —— 那需要疗效随访，库里一条都没有。
它做的是**库的画像**：哪些因子跟哪一经沾边、各沾多少。是描述，不是判别。

三种产出，一个比一个深：
  ① 六经·因子剖面      P(因子|经) —— 原文写了六经的案子里，每个因子出现的比例
  ② 判别力排序          互信息 I(因子;经) —— 「知道这条，我对是哪一经的判断能准多少」
  ③ 因子×因子 共现矩阵   哪些因子稳定同现 —— 教科书判据失效之后，「实际反复出现的组合」长什么样

Y 只用 `依据=原文` 的六经（157 案）——模型推断的那批拿来做这个等于自己拟合自己。
太阴原文批一条都没有，就当没有，别硬凑。

用法：py 因子分析.py [输出.md]
"""
import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

经序 = ["太阳", "阳明", "少阳", "太阴", "少阴", "厥阴"]


def 取():
    con = sqlite3.connect(DB)
    案因子 = defaultdict(set)
    for 案, 值 in con.execute("SELECT 医案id,因子 FROM 医案因子 WHERE 维度 IN ('症状','舌脉')"):
        案因子[案].add(值)
    案经 = defaultdict(set)
    for 案, 值 in con.execute("SELECT 医案id,因子 FROM 医案因子"
                              " WHERE 维度='六经' AND 来源='原文'"):
        案经[案].add(值)
    词频 = Counter(r[0] for r in con.execute(
        "SELECT 因子 FROM 医案因子 WHERE 维度 IN ('症状','舌脉')"))
    con.close()
    return 案因子, 案经, 词频


def 主经(案经):
    """多经案 → 取一个主经，按经序的第一个（这版先粗分，合传病是另一件事）"""
    return {案: sorted(s, key=lambda e: 经序.index(e) if e in 经序 else 99)[0]
            for 案, s in 案经.items() if s}


def 六经剖面(案因子, 案经, 词频):
    """P(因子|经) —— 每经里出现率最反常的因子"""
    主 = 主经(案经)
    纳入 = {案 for 案 in 主 if 案 in 案因子 and 案因子[案]}
    各经案 = defaultdict(list)
    for 案 in 纳入:
        各经案[主[案]].append(案)
    全体 = [案 for 案 in 案因子 if 案因子[案]]
    全体率 = {f: sum(1 for a in 全体 if f in 案因子[a]) / len(全体)
              for f, c in 词频.items() if c >= 5}

    剖 = {}
    for 经, 案集 in 各经案.items():
        if len(案集) < 5:
            continue
        行 = []
        for f in 词频:
            if 词频[f] < 5:
                continue
            n = sum(1 for a in 案集 if f in 案因子[a])
            if n == 0:
                continue
            p = n / len(案集)
            行.append((f, n, round(p, 3), round(p / max(全体率.get(f, 1e-9), 1e-9), 2)))
        行.sort(key=lambda x: -x[3])          # 按「比全体高多少倍」排
        剖[经] = dict(案数=len(案集), 行=行)
    return 剖, 各经案, 全体率


def 互信息(案因子, 案经, 词频, 各经案):
    """I(因子;经) —— 只取样本量够的经；多经案在每一经都算"""
    出 = {}
    for 经, 案集 in 各经案.items():
        if len(案集) < 10:                    # 样本太小的经先不算
            continue
        pos = set(案集)
        总 = [案 for 案 in 案因子 if 案因子[案]]
        行 = []
        for f, c in 词频.items():
            if c < 5:
                continue
            with_f = {案 for 案 in 总 if f in 案因子[案]}
            a = len(with_f & pos)
            b = len(with_f - pos)
            cc = len(pos) - a
            d = len(总) - a - b - cc
            n = a + b + cc + d
            if a == 0 or b == 0 or cc == 0 or d == 0 or n == 0:
                continue

            def px(x):
                return x / n if x else 1e-12
            mi = (px(a) * math.log2(px(a) / (px(a + b) * px(a + cc)))
                  + px(b) * math.log2(px(b) / (px(a + b) * px(b + d)))
                  + px(cc) * math.log2(px(cc) / (px(a + cc) * px(cc + d)))
                  + px(d) * math.log2(px(d) / (px(b + d) * px(cc + d))))
            行.append((f, a, cc, round(mi, 4)))
        行.sort(key=lambda x: -x[3])
        出[经] = 行
    return 出


def 共现(案因子, 词频, 上限=12, 门槛=8):
    """因子×因子 —— 两个因子都 >=5 次才看；按 Jaccard 排，只留前若干对"""
    因子集 = {f for f, c in 词频.items() if c >= 5}
    案 = {a: {f for f in fs if f in 因子集} for a, fs in 案因子.items() if fs}
    计 = defaultdict(int)
    带f的案 = {f: sum(1 for fs in 案.values() if f in fs) for f in 因子集}
    for fs in 案.values():
        fs = sorted(fs)
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                计[(fs[i], fs[j])] += 1
    行 = []
    for (x, y), n in 计.items():
        if n < 门槛:
            continue
        j = n / (带f的案[x] + 带f的案[y] - n)
        行.append((x, y, n, round(j, 3)))
    行.sort(key=lambda x: (-x[3], -x[2]))
    return 行[:上限]


def 写报告(剖, mi, 共, 各经案, 案经, 词频, 路径):
    with open(路径, "w", encoding="utf-8") as w:
        w.write("# 因子分析 —— 从一致因子集合里找「哪些因素真的影响判断」\n\n")
        w.write("**这版是库的画像，不是判别器** —— 预测「该开哪个方」需要疗效随访，库里一条都没有。\n\n")
        w.write("Y 只用 `依据=原文` 的六经（157 案）；太阴原文批一条都没有，就当没有。\n\n")

        w.write("## ① 各经的因子剖面（按「比全体高多少倍」排）\n\n")
        w.write("全体出现率 = 该因子在全部 965 个有标签案里的占比。\n\n")
        for 经 in 经序:
            if 经 not in 剖:
                continue
            p = 剖[经]
            w.write("### %s（%d 案）\n\n" % (经, p["案数"]))
            w.write("| 因子 | 该经出现 | 出现率 | 比全体高 |\n|---|---|---|---|\n")
            for f, n, r, lift in p["行"][:12]:
                w.write("| %s | %d | %.2f | ×%.2f |\n" % (f, n, r, lift))
            w.write("\n")

        w.write("## ② 判别力排序（互信息 I(因子;经)，只算样本≥10的经）\n\n")
        for 经, 行 in mi.items():
            w.write("### %s\n\n" % 经)
            w.write("| 因子 | 该病案里有 | 该病案里没有 | 互信息 |\n|---|---|---|---|\n")
            for f, a, c, v in 行[:12]:
                w.write("| %s | %d | %d | %.4f |\n" % (f, a, c, v))
            w.write("\n")

        w.write("## ③ 稳定共现的因子对（Jaccard 前 12）\n\n")
        w.write("| 因子甲 | 因子乙 | 同现案数 | Jaccard |\n|---|---|---|---|\n")
        for x, y, n, j in 共:
            w.write("| %s | %s | %d | %.3f |\n" % (x, y, n, j))
        w.write("\n")
        w.write("---\n*由 因子分析.py 生成，%d 案 / %d 个 ≥5 次因子 / 原文六经 %d 案。*\n"
                % (sum(len(v) for v in 各经案.values()),
                   len([f for f, c in 词频.items() if c >= 5]),
                   len(案经)))


def main():
    路径 = os.path.join(HERE, "因子分析.md")
    if len(sys.argv) > 1:
        路径 = sys.argv[1]

    案因子, 案经, 词频 = 取()
    print("有标签的案子 %d，其中原文写了六经的 %d" % (len(案因子), len(案经)))

    剖, 各经案, 全体率 = 六经剖面(案因子, 案经, 词频)
    mi = 互信息(案因子, 案经, 词频, 各经案)
    共 = 共现(案因子, 词频)

    print("六经剖面覆盖：%s" % "、".join("%s(%d案)" % (k, len(v)) for k, v in 各经案.items()))
    print("互信息算了 %d 经" % len(mi))
    print("共现对（Jaccard≥8案）前 12 里最强的 3 对：")
    for x, y, n, j in 共[:3]:
        print("   %s + %s（%d 案，J=%.3f）" % (x, y, n, j))

    写报告(剖, mi, 共, 各经案, 案经, 词频, 路径)
    print("\n写了 %s" % os.path.basename(路径))


if __name__ == "__main__":
    main()
