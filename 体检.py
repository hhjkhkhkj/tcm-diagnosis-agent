# -*- coding: utf-8 -*-
"""体检 —— 把「静默失败」变成「响的」

## 为什么要它

2026-10-14 一天里修了 12 个 bug，**没有一个会报错**。
它们全都长这样：能跑、不报错、输出看着正常——**只是悄悄少了一半数据**。

    「按方名过滤」把 951 例挡在检索外（可检索病案 965 → 50）
    「同 pdf 页码相近去重」把 951 例并成 1 条
    「只注入硬性=是的安全规则」→ 6 条里只进了 1 条
    「关键词路由」66 条里大半指向没入库的文件，全空

**这类 bug「跑一下看看」是看不出来的。** 所以不能靠"我小心点"，
要把「少了」变成「响的」——每条规则都量个数，对不上就喊。

## 用法

    py _体检.py

**每次改完检索/抽取/读数据的代码，跑一次。** 数对不上就是有问题。
"""
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

问题 = []


def 查(名, 实际, 合格, 说明=""):
    """合格：callable(实际) -> bool"""
    ok = 合格(实际)
    print("  %s %-30s %s%s" % ("✓" if ok else "✗", 名, 实际,
                              ("   ← " + 说明) if (说明 and not ok) else ""))
    if not ok:
        问题.append((名, 实际, 说明))


def main():
    con = sqlite3.connect(DB)
    print("=" * 62)
    print("中医大脑 · 体检")
    print("=" * 62)

    # ── 一、数据量（少一大截就是被静默过滤了）──
    print("\n【一】数据量")
    案因 = con.execute("SELECT COUNT(*) FROM 医案因子").fetchone()[0]
    查("医案因子行数", 案因, lambda v: v > 11000, "少了说明抽取/归一没跑全")

    测 = sqlite3.connect(DB)
    测.row_factory = sqlite3.Row
    try:
        from 检索 import 中医库
        库 = 中医库()
        查("可检索病案", len(库.可检索病案), lambda v: v > 900,
           "★ 2026-10-14 踩过：载入层一个「方名非空」过滤把它砍到 50")
    except Exception as e:
        print("  ✗ 中医库 载不起来：%s" % str(e)[:80])
        问题.append(("中医库载入", "异常", str(e)[:80]))

    受控 = os.path.join(HERE, "_受控词表.json")
    if os.path.exists(受控):
        import json as _json
        d = _json.load(open(受控, encoding="utf-8"))
        查("面板发的词表（_受控词表.json）", len(d.get("舌脉", [])) + len(d.get("症状", [])),
            lambda v: v >= 100, "面板「查/问诊」勾选用的就是这一套")

    # ── 二、归一是否真做了 ──
    # ⚠️ 只能数「症状/舌脉」这两个维度 —— 六经/体质/治法/病程是抽取来的，
    #    混进来会把数撑到 990 多，看不出归一有没有做（第一版就写错了）
    print("\n【二】归一")
    因 = con.execute("SELECT COUNT(DISTINCT 因子) FROM 医案因子"
                     " WHERE 维度 IN ('症状','舌脉')").fetchone()[0]
    查("症状/舌脉 的 distinct 因子", 因, lambda v: 200 <= v <= 400,
       ">400 说明归一没做（变体没并）；<150 说明并过头了")
    高 = con.execute("SELECT COUNT(*) FROM (SELECT 因子 FROM 医案因子"
                     " WHERE 维度 IN ('症状','舌脉')"
                     " GROUP BY 因子 HAVING COUNT(*)>=5)").fetchone()[0]
    查("高频因子(>=5次)", 高, lambda v: v >= 100, "建模的可用面")

    # ── 三、安全底线（今天踩过：只进了 1/6）──
    print("\n【三】安全底线")
    try:
        from 模型判断 import 安全底线
        t = 安全底线()
        n = t.count("\n- ")
        查("注入推演层的安全规则条数", n, lambda v: v >= 5,
           "★ 2026-10-14 踩过：默认只给「硬性=是」的，6 条里只进了 1 条")
    except Exception as e:
        print("  ✗ 安全底线 取不到：%s" % str(e)[:80])
        问题.append(("安全底线", "异常", str(e)[:80]))

    # ── 四、死链扫描（指向字段为空 = 桥是断的）──
    print("\n【四】死链（「指向」字段为空）")
    表 = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for t in 表:
        列 = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
        if "目标id" in 列:
            空 = con.execute("SELECT COUNT(*) FROM %s WHERE 目标id IS NULL" % t).fetchone()[0]
            总 = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            查("%s 目标id 为空" % t, "%d/%d" % (空, 总), lambda v: int(v.split("/")[0]) == 0,
               "★ 2026-10-14 发现：关键词路由 59/66 指向没入库的文件")

    # ── 五、新增索引的覆盖率（今天刚加的，会随数据变）──
    print("\n【五】新索引覆盖率")
    try:
        from 中医大脑面板 import 因子指向, 共现索引
        查("有六经指向的因子", len(因子指向), lambda v: v > 0, "面板启动时算的")
        查("有共现伙伴的因子", len(共现索引), lambda v: v > 0, "面板启动时算的")
    except Exception as e:
        print("  （面板索引取不到，跳过：%s）" % str(e)[:60])

    # ── 六、库表总览 ──
    print("\n【六】库表总览")
    空表 = [t for t in 表
            if con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] == 0]
    print("  表 %d 张；空的 %d 张：%s"
          % (len(表), len(空表), "、".join(空表) if 空表 else "（无）"))
    print("  （空表不一定是问题 —— 像 `我的病案` 就是在等第一条）")

    con.close()
    print("\n" + "=" * 62)
    if 问题:
        print("⚠️ %d 项不合格：" % len(问题))
        for 名, 实际, 说明 in 问题:
            print("   · %s = %s%s" % (名, 实际, ("   ← " + 说明) if 说明 else ""))
    else:
        print("全过。")
    print("=" * 62)


if __name__ == "__main__":
    main()
