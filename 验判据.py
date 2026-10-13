# -*- coding: utf-8 -*-
"""验判据 —— 拿书里**已经写好的**六经判据，去 951 个病案上量它到底准不准

## 它跟「从数据里挖权重」有什么不同

挖权重做不了（图里没有疗效、没有会变的结果）。但**书里本来就印着判据**——
T2 `六经公式` 那 7 条就是「症状组合 → 哪一经」，还带一条排除规则。
**书上没写的是这些判据的实际命中率和误判率。** 这个脚本量的就是这个，是**新增的知识**。

## 那座桥（这一步的关键）

判据写的是《伤寒论》的话（`脉浮`、`恶寒`、`四肢厥冷`），
库里存的是书里的症状词（`浮脉`、`身冷畏寒`、`四肢厥冷手脚冰冷`）——**两边对不上**。
`检索.py` 里的 `SYN` 同义词表就是桥，但**必须用包含匹配**：
两边粒度不一样（`身冷` vs `身冷畏寒`），精确匹配一条都中不了。

## 判据怎么算分

对每条判据：
    命中案   = 症状组里每个标签都能在案子里找到说法（**集合匹配**，缺一个就不算）
    有标注   = 命中案里，那些**原文自己说了六经**的（187 条那批，是唯一可信的 Y）
    命中率   = 命中案里判定与原文一致的 / 有标注的命中案
    漏检     = 原文是这一经、判据却没命中的案子
    漏检率   = 漏检 / 该经全部有标注的案子

**只跟「依据=原文」的六经比** —— 模型推断出来的那批拿来做这个，等于自己拟合自己。

用法：py _验判据.py
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

from 检索 import SYN   # noqa: E402
import 因子分析 as A        # noqa: E402  （只用它的数据指纹）


def 展开(标签):
    """标签 → 一组说法（拿 SYN 双向认：命中 key、或正好是某个 value，都把这一簇互相展开）"""
    出 = {标签}
    for k, vs in SYN.items():
        if 标签 == k or 标签 in vs:
            出.add(k)
            出.update(vs)
    return {x for x in 出 if x}


缓存 = {}


def 命中(因子集, 标签):
    """案子里有没有这个标签（包含匹配，双向）"""
    if 标签 not in 缓存:
        缓存[标签] = 展开(标签)
    for s in 缓存[标签]:
        for f in 因子集:
            if s in f or f in s:
                return True
    return False


def main():
    con = sqlite3.connect(DB)

    案因子 = defaultdict(set)
    for 案, 值 in con.execute("SELECT 医案id,因子 FROM 医案因子 WHERE 维度 IN ('症状','舌脉')"):
        案因子[案].add(值)

    案经 = defaultdict(set)      # 只收「依据=原文」——模型推断的那批不能当 Y
    for 案, 值 in con.execute("SELECT 医案id,因子 FROM 医案因子"
                              " WHERE 维度='六经' AND 来源='原文'"):
        案经[案].add(值)

    公式 = [dict(id=r[0], 类型=r[1], 症状组=json.loads(r[2] or "[]"),
                 判定=r[3], 排除=r[4]) for r in
            con.execute("SELECT id,类型,症状组,判定,排除 FROM 六经公式")]

    print("有标签的案子 %d ／ 其中原文写了六经的 %d 案" % (len(案因子), len(案经)))
    print()

    行 = []
    for f in 公式:
        经 = re.sub(r"病$", "", f["判定"] or "")
        命中案 = {案 for 案, fs in 案因子.items()
                  if fs and all(命中(fs, t) for t in f["症状组"])}
        # 逐词命中数 —— 判据成立不了，到底是**整条没用**还是**卡在某个词上**？
        # 这个细分才可行动：卡在哪个词，就去词表里补那个词的说法
        逐词 = {t: sum(1 for fs in 案因子.values() if 命中(fs, t)) for t in f["症状组"]}
        有标 = 命中案 & set(案经)
        对 = {案 for 案 in 有标 if 经 in 案经[案]}

        该经全案 = {案 for 案, 经集 in 案经.items() if 经 in 经集}
        漏 = 该经全案 - 命中案

        行.append(dict(f, 经=经, 命中=len(命中案), 有标=len(有标), 对=len(对),
                       该经全案=len(该经全案), 漏=len(漏), 逐词=逐词,
                       命中率=(round(len(对) / len(有标), 2) if 有标 else None),
                       漏检率=(round(len(漏) / len(该经全案), 2) if 该经全案 else None),
                       对案=sorted(对), 漏案=sorted(漏)[:8]))

    行.sort(key=lambda r: (-r["有标"], r["id"]))
    print("%-4s %-4s %-26s %-6s %5s %5s %6s %8s %8s"
          % ("id", "类型", "症状组", "判定", "命中", "有标", "对", "命中率", "漏检率"))
    for r in 行:
        print("%-4s %-4s %-26s %-6s %5d %5d %6d %8s %8s"
              % (r["id"], r["类型"], "、".join(r["症状组"])[:24], r["判定"],
                 r["命中"], r["有标"], r["对"],
                 r["命中率"] if r["命中率"] is not None else "—",
                 r["漏检率"] if r["漏检率"] is not None else "—"))

    print("\n=== 逐词诊断：每个词各自能命中多少案（0 = 整条判据就卡在这个词上）===")
    for r in 行:
        print("  [%s] %s" % (r["id"], r["判定"]))
        for t, n in r["逐词"].items():
            print("      %-10s %4d 案%s" % (t, n, "   ← 卡在这" if n == 0 else ""))

    # 报告
    p = os.path.join(HERE, "_判据验证.md")
    with open(p, "w", encoding="utf-8") as w:
        w.write("# 六经判据验证（拿 951 案去量书里写好的 7 条判据）\n\n")
        w.write(A.指纹行())
        w.write("只跟 `依据=原文` 的六经比（%d 案）——模型推断的那批是循环论证，不能当 Y。\n\n"
                % len(案经))
        w.write("| id | 类型 | 症状组 | 判定 | 命中案 | 有原文标注 | 判对 | 命中率 | 漏检案 | 漏检率 |\n")
        w.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in 行:
            w.write("| %s | %s | %s | %s | %d | %d | %d | %s | %d | %s |\n"
                    % (r["id"], r["类型"], "、".join(r["症状组"]), r["判定"],
                       r["命中"], r["有标"], r["对"],
                       r["命中率"] if r["命中率"] is not None else "—",
                       r["漏"], r["漏检率"] if r["漏检率"] is not None else "—"))
        w.write("\n## 逐条明细\n\n")
        for r in 行:
            w.write("### [%s] %s —— %s\n\n" % (r["id"], r["类型"], r["判定"]))
            w.write("- 症状组：%s\n" % "、".join(r["症状组"]))
            if r["排除"]:
                w.write("- **排除结论**：%s\n" % r["排除"])
            w.write("- 命中 %d 案，其中有原文六经标注的 %d 案，判对 %d 案\n"
                    % (r["命中"], r["有标"], r["对"]))
            if r["对案"]:
                w.write("- 判对的案 id：%s\n" % "、".join(map(str, r["对案"][:20])))
            if r["漏案"]:
                w.write("- 漏检的案 id（原文是%s、判据没中）：%s\n"
                        % (r["经"], "、".join(map(str, r["漏案"]))))
            w.write("- 逐词命中：%s\n"
                    % "、".join("%s(%d案)" % (t, n) for t, n in r["逐词"].items()))
            w.write("\n")
        w.write("## 原文六经的分布（Y 的样本量）\n\n")
        for k, v in Counter(x for s in 案经.values() for x in s).most_common():
            w.write("- %s：%d 条\n" % (k, v))
    print("\n写了 _判据验证.md")
    con.close()


if __name__ == "__main__":
    main()
