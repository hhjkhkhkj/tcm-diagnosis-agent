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
import glob
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

问题 = []
待办 = []          # ⚠️ 报了但不该判"不合格"的（需要人决定，比如「这张表要不要接线」）。
                   #    它们**必须出现在最后的总结里** —— 否则就是体检自己犯它开篇骂的那个错：
                   #    「报告了，但汇总当没看见」。2026-10-15 加。

# ── 已拍板「暂存不用」的表：有数据、也有**明确理由**不用 ──
# ⚠️ 只有**人拍过板**的才能进这里，且必须写清日期和理由。
#    进来的表，【七】打「已记账」而不是 ⚠️ ——
#    因为**一条永远消不掉的告警，等于训练人去忽略告警**，
#    那跟「能被满足的检查」是一对孪生的错。
暂存不用 = {
    "针灸处方": "2026-10-15 定。① 手法/依据 两列 2227 行 100% 为空；"
                "② 穴 列 451/2227 是句子残片（「故用下此二穴除胸腔瘀血」）；"
                "③ 针灸已有覆盖 —— 那 9 条路由已接上《针灸大成》九个门。"
                "要接线得先重抽。见 库扩展.py T15 注释。",
}


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
        # ⚠️ 这条原来判的是 `v >= 100` —— **只验证"这文件不是空的"，不验证"它是不是当前的"**。
        #    于是 `_受控词表.json` 停在 10-11（113 个词），而词表归一 10-13 早就做到 247 了，
        #    检查照样打 ✓，**模型三年挑不到另外 134 个因子**。
        #    **能被满足的检查 ≠ 对的检查**（今天第 4 次同一个形状）。
        #    改成拿库里的规范因子**逐条比对** —— 多一个少一个都报。
        import json as _json
        d = _json.load(open(受控, encoding="utf-8"))
        面 = set(d.get("舌脉", [])) | set(d.get("症状", []))
        库 = {r[0] for r in con.execute(
            "SELECT DISTINCT 因子 FROM 医案因子 WHERE 维度 IN ('症状','舌脉')")}
        _少 = sorted(库 - 面)
        _多 = sorted(面 - 库)
        查("面板词表 少的因子（模型挑不到 → 检索就漏）", len(_少), lambda v: v == 0,
           "、".join(_少[:8]) + ("…" if len(_少) > 8 else "") if _少 else "")
        查("面板词表 多的因子（库里没有的选项）", len(_多), lambda v: v == 0,
           "、".join(_多[:8]) if _多 else "")

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

    # ── 四、死链扫描 ──
    # ⚠️ 2026-10-15 大修。原来这里只查 `目标id IS NULL` —— **只问"填了没有"，不问"填的对不对"。**
    #    实测：66 条关键词路由里 **33 条的目标表是错的**。病因是**名同物异**：
    #    「针灸」既是路由的 `类别`，也是一张**表名**；回填脚本拿 `类别` 去填 `目标表`，
    #    于是 id（一直是文献块 id）和表名对不上。六经/本草/医案/剂量_规范 同理。
    #    后果：`检索.py` 的 `路由反查()` 只认 `目标表='文献块'`，那 33 条**从没被读过**。
    #    而这条检查全打 ✓ —— 一条都没拦住。**「不是空」是最松的那种满足。**
    #    现在：① 目标表必须是库里真实存在的表/视图；② 目标id 必须在目标表里真的存在。
    print("\n【四】死链（指向的东西存不存在）")
    表 = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    对象名 = sorted({r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")})
    for t in 表:
        列 = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
        if "目标id" in 列:
            空 = con.execute("SELECT COUNT(*) FROM %s WHERE 目标id IS NULL" % t).fetchone()[0]
            总 = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            查("%s 目标id 为空" % t, "%d/%d" % (空, 总), lambda v: int(v.split("/")[0]) == 0,
               "★ 2026-10-14 发现：关键词路由 59/66 指向没入库的文件")
        if {"目标表", "目标id"} <= set(列):
            _乱 = con.execute(
                "SELECT COUNT(*) FROM %s WHERE COALESCE(目标表,'')<>''"
                " AND 目标表 NOT IN (%s)" % (t, ",".join("?" * len(对象名))),
                对象名).fetchone()[0]
            查("%s 目标表 指向不存在的表" % t, _乱, lambda v: v == 0,
               "声明了一个库里没有的表名")
            _断 = 0
            for _r in con.execute(
                    "SELECT 目标表,目标id FROM %s WHERE 目标id IS NOT NULL" % t):
                if _r[0] not in 对象名:
                    continue                       # 上一条已经报过了
                try:
                    if not con.execute("SELECT 1 FROM %s WHERE id=?" % _r[0],
                                       (_r[1],)).fetchone():
                        _断 += 1
                except sqlite3.Error:               # 那张表没有 id 列
                    _断 += 1
            查("%s 目标id 在目标表里查无此行" % t, _断, lambda v: v == 0,
               "★ 2026-10-15 踩过：33 条路由目标表填错（名同物异），这条原来全打 ✓")

        # 路由表专有：`路由反查()` 只认 `目标表='文献块'`（见 检索.py:452）。
        # 别的目标表**填得再对也不会被读** —— 那是"存了但没接线"，不是数据错，
        # 所以进「待办」不进「不合格」。（改之前这里是 33 条，见 _修路由2.py。）
        if t == "关键词路由":
            # ⚠️ 出处里带「⚠️暂存」的行**不算** —— 那是已经记账的（见 _修路由2.py / 交接文档 9.4）。
            #    没记账的才算待办。
            _外 = con.execute(
                "SELECT COUNT(*) FROM 关键词路由 WHERE COALESCE(目标表,'')<>'文献块'"
                " AND COALESCE(出处,'') NOT LIKE '%⚠️暂存%'").fetchone()[0]
            _记 = con.execute(
                "SELECT COUNT(*) FROM 关键词路由 WHERE COALESCE(目标表,'')<>'文献块'"
                " AND COALESCE(出处,'') LIKE '%⚠️暂存%'").fetchone()[0]
            if _记:
                print("  ○ 另有 %d 条路由「暂存」不读（已记账）" % _记)
            if _外:
                # ⚠️ 这个 con 没设 row_factory（脚本里另开的 `测` 才设），取的是元组，别写 dict(x)
                行 = list(con.execute(
                    "SELECT id,关键词,目标表,目标id FROM 关键词路由"
                    " WHERE COALESCE(目标表,'')<>'文献块'"))
                print("\n  ⚠️ %d 条路由的目标表不是 `文献块`，路由反查读不到它们：" % _外)
                for _i, _词, _tb, _tid in 行:
                    print("     #%s %s → %s id=%s" % (_i, (_词 or "")[:24], _tb, _tid))
                待办.append(("路由反查读不到的路由", "%d 条（%s）"
                             % (_外, "、".join("#%s" % x[0] for x in 行)),
                             "接进 路由反查，或改指文献块，或删"))
            else:
                print("\n  ✓ 每条路由的目标表都是 路由反查 会读的")

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
    断, 记账 = [], []
    for t in 表:
        n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        if n == 0:
            continue
        if t not in 可达:
            if t in 暂存不用:
                记账.append((t, n))
            else:
                断.append((t, n))
    if 断:
        for t, n in 断:
            print("  ⚠️ %-16s %5d 行" % (t, n))
        print("  ⚠️ 上面这些表，**模型永远看不到** —— 检索不读就等于没有。")
        print("     接线（像 `验方反查` 接 T17），或明确记下「暂存不用」；别默认它在起作用。")
        待办.append(("有数据但活路径没人读的表", "%s（共 %d 行）"
                     % ("、".join(t for t, _ in 断), sum(n for _, n in 断)),
                     "接线，或记下「暂存不用」"))
    if 记账:
        # ⚠️ **不报成 ⚠️**，但仍然印出来 —— 目的是让"没接线"这件事一直看得见，
        #    而不是让它悄悄变成"看起来在工作"。（2026-10-15 加，见文件顶部 暂存不用）
        for t, n in 记账:
            print("  ○ %-16s %5d 行  ← 已记账「暂存不用」，不是遗漏" % (t, n))
            print("      理由：%s" % 暂存不用[t])
    if not 断 and not 记账:
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
        待办.append(("视图没人读、底表被读（读的是半截）", "、".join(v for v, _ in 疑), ""))
    else:
        print("  ✓ 没有这个问题")

    # ── 九、文档 vs 代码 ──
    # 2026-10-14 加。**一天里被抓出四次「文档说的和代码做的不一样」**：
    #   ① README 说会诊/抽取用 flash，实际两层都是 kimi-k3
    #   ② config.example.py 教人配 LLM_URL，而代码读的是 推演_URL
    #      —— 照它配会被**静默忽略、悄悄退回 DeepSeek，且不报错**（最坏的一种）
    #   ③ 17 个 .py 在仓库里，README 一个字没提
    #   ④ README 说「不能定方」，代码里写着「你有处方权」—— **定位级**的错
    # **这四条全都能自动查。** 手工核对靠不住：文档是手写的，代码是改的，两边必然漂。
    import re as _re2
    仓 = r"D:\Download\tcm-diagnosis-agent"
    print("\n【九】文档 vs 代码")
    if not os.path.isdir(仓):
        print("  （找不到仓库目录，跳过）")
    else:
        def _读(p):
            return open(p, encoding="utf-8", errors="ignore").read()
        rm = os.path.join(仓, "README.md")
        if not os.path.exists(rm):
            print("  ⚠️ 仓库里没有 README.md")
            r = ""
        else:
            r = _读(rm)
        if r:
            仓py = {os.path.basename(p) for p in glob.glob(os.path.join(仓, "*.py"))}
            没提 = sorted(f for f in 仓py if f not in r)
            查("仓库的 .py 都被 README 提到", len(没提), lambda v: v == 0,
               ("、".join(没提[:6]) + ("…" if len(没提) > 6 else "")) if 没提 else "")
            引用 = {x for x in _re2.findall(r"`([^`\s]+\.py)`", r)} - {"config.py"}
            缺 = sorted(f for f in 引用 if not os.path.exists(os.path.join(仓, f)))
            查("README 引用的 .py 都存在", len(缺), lambda v: v == 0,
               "、".join(缺[:6]) if 缺 else "")

        # config.example.py 的键 vs 代码真正 `_取` 的键
        ex = os.path.join(仓, "config.example.py")
        要 = set()
        for f in 活:
            p = os.path.join(HERE, f)
            if os.path.exists(p):
                要 |= set(_re2.findall(r'_取\("([^"]+)"\)', _读(p)))
        if 要 and os.path.exists(ex):
            有 = set(_re2.findall(r"^([A-Za-z_\u4e00-\u9fa5][\w\u4e00-\u9fa5]*)\s*=", _读(ex), _re2.M))
            查("config.example 少的键（代码会读但样板没有）", len(要 - 有), lambda v: v == 0,
               "、".join(sorted(要 - 有)) if 要 - 有 else "")
            查("config.example 多的键（样板教人配、代码不读）", len(有 - 要), lambda v: v == 0,
               "、".join(sorted(有 - 要)) if 有 - 要 else "")

        # README 里写的模型名，是否覆盖代码实际在用的
        if r:
            try:
                from 模型判断 import DS_MODEL, JEV_MODEL
                import config as _cfg
                模型名 = {DS_MODEL, JEV_MODEL,
                          getattr(_cfg, "推演_MODEL", None), getattr(_cfg, "抽取_MODEL", None)}
            except Exception:
                模型名 = set()
            低 = r.lower()
            漏 = sorted(m for m in 模型名 if m and m.split("/")[-1].replace("-latest", "") not in 低)
            查("README 覆盖了代码在用的每个模型", len(漏), lambda v: v == 0,
               "README 里没提：" + "、".join(漏) if 漏 else "")

        # 几个关键计数：README 里写的数，跟库里实际对不对
        if r:
            错 = []
            for 名, sql in [("文献块", "SELECT COUNT(*) FROM 文献块"),
                            ("医案", "SELECT COUNT(*) FROM 医案"),
                            # ⚠️ 这个数是**会变的**（重出词表时 247→241），加进来才拦得住漂
                            ("规范因子", "SELECT COUNT(DISTINCT 因子) FROM 医案因子"
                                     " WHERE 维度 IN ('症状','舌脉')")]:
                try:
                    n = con.execute(sql).fetchone()[0]
                except Exception:
                    continue
                if str(n) not in r:
                    错.append("%s 实际 %d，README 里找不到这个数" % (名, n))
            查("README 的关键计数跟库一致", len(错), lambda v: v == 0, "；".join(错))

    # ── 十、结构化字段里落单的 markdown 标记 ──
    # 2026-10-15 加。起因：抽取器把「**配对的标记**」当成「trim 星号」处理 ——
    #     '**分流处理**：常规用倪'.strip('*')  →  '分流处理**：常规用倪'
    # 于是 `场景路由.用谁` 有三行**只丢了开头那半个**，在提示词里就是一个配不成对的
    # `**`，纯噪音。**有源文件逐行作证**（_tcm_src/…/SKILL.md 的「核心立场融合」表，
    # 6/6 吻合），不是随机脏数据。修法见 `_补粗体标记.py`。
    # 判据：`**` 个数为**奇数** = 被吞了半个。
    #
    # ⚠️ **必须排掉原文大字段**：`医案.全文` / `文献块.正文` 里的星号是**原文自带的** ——
    #    星号横线（`***注意`、`******************`）、**故意打码的人名**（`学生王**`）、
    #    以及正文里的 OCR 噪声，一共 23 处。不排掉它就会永远误报 ——
    #    **一条永远消不掉的告警，等于训练人去忽略告警。**
    print("\n【十】结构化字段里落单的 `**`（被吞了半个标记）")
    _原文大列 = {"医案.全文", "文献块.正文"}
    _落 = []
    for t in 表:
        for c in [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]:
            if "%s.%s" % (t, c) in _原文大列:
                continue
            try:
                _行 = list(con.execute(
                    "SELECT [%s] FROM [%s] WHERE [%s] LIKE '%%**%%'" % (c, t, c)))
            except sqlite3.Error:
                continue
            _n = sum(1 for (_v,) in _行 if (_v or "").count("**") % 2 == 1)
            if _n:
                _落.append((t, c, _n))
    查("落单 `**` 的（表, 列）组合数", len(_落), lambda v: v == 0,
       "；".join("%s.%s ×%d" % x for x in _落) if _落 else "")

    con.close()
    print("\n" + "=" * 62)
    if 问题:
        print("⚠️ %d 项不合格：" % len(问题))
        for 名, 实际, 说明 in 问题:
            print("   · %s = %s%s" % (名, 实际, ("   ← " + 说明) if 说明 else ""))
    if 待办:
        # ⚠️ 2026-10-15 加。原来 七/八 打了 ⚠️ 却**不进任何汇总**，最后照样印「全过」——
        #    体检自己犯的正是它开篇骂的那个错：**报告了，但汇总当没看见**。
        print("%s %d 项待处理（不是错，是要人拍板）："
              % ("⚠️" if 问题 else "✅ 硬性检查全过，但仍有", len(待办)))
        for 名, 实际, 说明 in 待办:
            print("   · %s = %s%s" % (名, 实际, ("   ← " + 说明) if 说明 else ""))
    if not 问题 and not 待办:
        print("全过。")
    print("=" * 62)


if __name__ == "__main__":
    main()
