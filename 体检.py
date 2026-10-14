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

    # ── 七、有数据、但**活路径**里没人读的表 ──
    # 2026-10-14 加。起因：T17 `李可验方` 有 115 行，**全项目只有建表的 库扩展.py 引它** ——
    # 也就是那 115 首李可验方从没进过模型的检索材料，是「存了但没接线」。
    # 这类东西**不报错、不报警，只是白存**，跟静默丢数据是一个家族（一个是少给，一个是白给）。
    活 = ["检索.py", "中医大脑面板.py", "模型判断.py", "评测.py"]

    def 剥注释和文档(路径):
        """只留"真正会被执行到的文本" —— **注释和文档字符串全去掉**

        两个弯路都走过，记下来免得再走：
        ① 只做 substring（`表名 in 文件内容`）→ 我在 docstring 里写了一句
           「本草 4／针灸处方 9／剂量_规范 2」，那几个**真没接线**的表立刻被判成"读了"。
           **能被注释满足的检查就是在说谎。**
        ② 改成只认 `FROM/JOIN + 表名` 的正则 → 反了：`_载附属()` 用的是
           `"SELECT * FROM %s" % t`（**变量拼表名**），正则会把它读的
           `加减/方性/结构符合方/药对` 全判成死的。**误报也是说谎。**
        → 正解是看 token：注释去掉，**三引号文档字符串**去掉，其余原文照留
        （所以 `"加减"` 这种当数据用的字符串仍在，变量拼表名也抓得到）。
        """
        import io
        import tokenize
        try:
            with io.open(路径, encoding="utf-8", errors="ignore") as f:
                出 = []
                for k in tokenize.generate_tokens(f.readline):
                    if k.type == tokenize.COMMENT:
                        continue
                    if k.type == tokenize.STRING and k.string[:3] in ('"""', "'''"):
                        continue          # 文档字符串
                    出.append(k.string)
                return " ".join(出)
        except Exception:
            return open(路径, encoding="utf-8", errors="ignore").read()

    活文 = {}
    for f in 活:
        p = os.path.join(HERE, f)
        if os.path.exists(p):
            活文[f] = 剥注释和文档(p)
    import re as _re
    print("\n【七】有数据、但活路径四个文件里没人读的表")
    # ⚠️ 要算**传递**：代码读 `剂量_观测`（视图），视图 SQL 里引了 `剂量_观测_倪` ——
    #    那张表就是被读到的。只做直接匹配会把 `剂量_观测_倪` 误判成"没人读"（实测就是这样）。
    活文本 = " ".join(活文.values())
    # ⚠️ 传播的**起点必须包含视图** —— 只拿 `表`（type='table'）当起点的话，
    #    「代码读视图 → 视图引底表」这条链根本起不来。第一版就漏了这一步。
    对象 = 表 + [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")]
    视图名 = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    可达 = {t for t in 对象 if t in 活文本}
    变了 = True
    while 变了:                                  # 视图 → 底表，反复传到底
        变了 = False
        for v in list(可达):
            # ⚠️ **只有视图才「读」别人。** 表的 CREATE 语句里带注释（比如 `医案` 的建表 SQL
            # 里提了一句别的表名），拿它当"读了"就是**假阴性** —— 实测 `针灸处方`(2227 行)
            # 就是这么被判成"可达"、从名单上消失的。**建表注释里提一句什么都不算。**
            if v not in 视图名:
                continue
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (v,)).fetchone()
            if not sql or not sql[0]:
                continue
            for t in 对象:
                # 不用 `\b`：中文下边界靠不住（`剂量_观测` 会当成 `剂量_观测_倪` 的前缀）
                if t not in 可达 and t in sql[0]:
                    可达.add(t)
                    变了 = True
    断 = []
    for t in 表:
        n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        if n == 0:
            continue
        if t not in 可达:
            断.append((t, n))
    if 断:
        for t, n in 断:
            print("  ⚠️ %-16s %5d 行" % (t, n))
        print("  ⚠️ 上面这些表，**模型永远看不到** —— 检索不读就等于没有。")
        print("     接线（像 `验方反查` 接 T17），或明确记下「暂存不用」；别默认它在起作用。")
    else:
        print("  ✓ 有数据的表都在活路径里")

    # ── 八、视图没被读、底表却被读了 ──
    # 2026-10-14 加。起因：检索读的是 `剂量分布`（只有问止·克），而 `剂量_观测`
    # 是「问止 ∪ 倪海厦」的视图，**没人读** —— 于是倪的实测剂量从没到过模型眼前。
    # 形状是「**读了子集、漏了父集**」：**不报错，因为读的那张表本身是好的**，
    # 只是它不完整。这跟【七】的"完全没读"是两种病，症状却一样安静。
    import re as _re
    print("\n【八】视图没被读、底表却被读了（读的是半截）")
    视图 = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='view'")]
    疑 = []
    for v in 视图:
        if any(v in x for x in 活文.values()):
            continue                                   # 视图自己有人读 → 没问题
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name=?", (v,)).fetchone()[0] or ""
        底 = [t for t in 表 if t != v and _re.search(r"\b%s\b" % _re.escape(t), sql)]
        被读 = [t for t in 底 if any(t in x for x in 活文.values())]
        if 被读:
            疑.append((v, 被读))
    if 疑:
        for v, 底 in 疑:
            print("  ⚠️ 视图 `%s` 没人读，但它的底表 %s 在被读" % (v, "、".join(底)))
        print("  ⚠️ 很可能**读的是半截**：视图把多个来源并起来了，代码却只读了其中一张。")
    else:
        print("  ✓ 没有这个问题")

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
