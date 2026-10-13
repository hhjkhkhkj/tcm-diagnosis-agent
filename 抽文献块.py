# -*- coding: utf-8 -*-
"""⑦ 文献块 · 散文入库（T16）

原则（计划第 3 条）：**只灌高频篇**，不整吞 3.4MB。
入选标准 = 与「六经 / 方剂 / 剂量」直接相关，且是高频查阅对象。

纳入（共 14 个文件，约 2.8MB）：
  倪  01_shanghan_sun / 02_shanghan_other / 04_jingui / 09_zhenjiu_bencao
  李  01_yangqi ~ 06_maifa（全部）
  古籍 伤寒论 / 金匮要略 / 黄帝内经素问 / 黄帝内经灵枢

不纳入（低一档，需要时再单开）：
  倪 03_yian.md（951K，医案讲解 —— 病案已由 T13 覆盖）
  倪 05/08_huangdi_*（415K —— 内经原文已从 古籍/ 入）
  倪 06_liangdong / 07_bimen_hantang（77K，低频）
  古籍 医宗金鉴(3M)/神农本草经/脉经/针灸大成/… （本草已由 T12 覆盖，金鉴与伤寒金匮重复）

切块：有 `###` 就按 `###` 切，否则按 `##` 切；仍超过 6000 字的按空行再切。
       每条记 source / 书 / 篇（最近的 ## 祖先）/ 标题 / 正文 / 字数 / 关键词。
关键词：正文里命中的六经名 + 方剂表中的方名（JSON 数组），用于把文献块挂回 T3/T9。

幂等：先删 source IN ('ni','li','古籍') 再插。
"""
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
基 = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references"

文件 = [
    ("ni_depth/modules/01_shanghan_sun.md",     "ni", "倪海厦·伤寒论太阳病篇"),
    ("ni_depth/modules/02_shanghan_other.md",   "ni", "倪海厦·伤寒论其他篇"),
    ("ni_depth/modules/04_jingui.md",           "ni", "倪海厦·金匮要略"),
    ("ni_depth/modules/09_zhenjiu_bencao.md",   "ni", "倪海厦·针灸本草"),
    ("li_depth/modules/01_yangqi.md",           "li", "李可·阳气理论"),
    ("li_depth/modules/02_shanghan.md",         "li", "李可·伤寒论"),
    ("li_depth/modules/03_jingfang.md",         "li", "李可·经方"),
    ("li_depth/modules/04_peiyuan.md",          "li", "李可·培元固本"),
    ("li_depth/modules/05_zhenghou.md",         "li", "李可·证候"),
    ("li_depth/modules/06_maifa.md",            "li", "李可·脉法"),
    ("古籍/伤寒论.md",                          "古籍", "伤寒论"),
    ("古籍/金匮要略.md",                        "古籍", "金匮要略"),
    ("古籍/黄帝内经素问.md",                    "古籍", "黄帝内经素问"),
    ("古籍/黄帝内经灵枢.md",                    "古籍", "黄帝内经灵枢"),
]

六经名 = ["太阳", "阳明", "少阳", "太阴", "少阴", "厥阴"]
最大字 = 6000          # 单块上限，超了按空行再切


def 取方名(con):
    try:
        return sorted({r[0] for r in con.execute("SELECT 方名 FROM 方剂")
                       if r[0] and len(r[0]) >= 3}, key=len, reverse=True)
    except Exception:
        return []


def 抽关键词(正文, 方名):
    hit = [j for j in 六经名 if j in 正文]
    hit += [f for f in 方名 if f in 正文][:12]
    return json.dumps(hit, ensure_ascii=False) if hit else None


def 切大块(标题, 正文):
    """超过最大字的块，按空行段落再切，标题带 (i/n)"""
    if len(正文) <= 最大字:
        return [(标题, 正文)]
    paras = [p.strip() for p in re.split(r"\n\s*\n", 正文) if p.strip()]
    组, 缓, 长 = [], [], 0
    for p in paras:
        if 缓 and 长 + len(p) > 最大字:
            组.append("\n\n".join(缓))
            缓, 长 = [], 0
        缓.append(p)
        长 += len(p) + 2
    if 缓:
        组.append("\n\n".join(缓))
    n = len(组)
    return [("%s（%d/%d）" % (标题, i + 1, n), g) for i, g in enumerate(组)]


def 抽文件(路径, 方名):
    文本 = open(路径, encoding="utf-8").read()
    行 = 文本.splitlines()
    切三级 = any(l.startswith("### ") for l in 行)
    切点 = "### " if 切三级 else "## "

    块 = []
    fa = None          # 最近的 ## 祖先
    cur = None         # 当前 (标题, 起行)
    buf = []

    def 收():
        if cur and buf:
            t = "\n".join(buf).strip()
            if t:
                # 正文里的小标题留在内容里，读起来才有上下文
                块.append((fa, cur[0], t, cur[1]))

    for i, l in enumerate(行):
        is2 = l.startswith("## ") and not l.startswith("### ")
        if is2:
            # 章节级先起一块；下面若出现 ### 再被更细的块接管
            收()
            fa = l[3:].strip()
            cur, buf = (fa, i + 1), []
            continue
        if 切三级 and l.startswith("### "):
            收()
            cur, buf = (l[4:].strip(), i + 1), []
            continue
        if cur is not None:
            buf.append(l)
    收()

    out = []
    for fa, 标题, 正文, 起行 in 块:
        for t, v in 切大块(标题, 正文):
            # 章级块：标题即篇名；子块：篇取最近的 ## 祖先
            篇 = fa or 标题
            out.append((篇, t, v, 起行))
    return out


def main():
    con = sqlite3.connect(DB)
    try:
        方名 = 取方名(con)
        con.execute("DELETE FROM 文献块 WHERE source IN ('ni','li','古籍')")

        n = 0
        汇总 = {}
        for 相对, src, 书 in 文件:
            p = os.path.join(基, 相对.replace("/", os.sep))
            if not os.path.exists(p):
                print("  ⚠️ 缺文件：%s" % 相对)
                continue
            recs = 抽文件(p, 方名)
            rows = []
            for i, (篇, 标题, 正文, 起行) in enumerate(recs, 1):
                rows.append((src, 书, 篇, str(i), 标题, 正文, len(正文),
                             抽关键词(正文, 方名), "%s#L%d" % (相对, 起行)))
            con.executemany(
                "INSERT INTO 文献块(source,书,篇,条号,标题,正文,字数,关键词,出处)"
                " VALUES(?,?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
            汇总[书] = len(rows)
        con.commit()

        字 = con.execute("SELECT SUM(字数) FROM 文献块").fetchone()[0] or 0
        print("  文献块 %d 条 ｜ 共 %d 字（%.1f MB）" % (n, 字, 字 / 1048576.0))
        print("\n=== 按书 ===")
        for 书, c in sorted(汇总.items(), key=lambda x: -x[1]):
            print("  %-22s %d 条" % (书, c))

        print("\n=== 按 source ===")
        for r in con.execute("SELECT source,COUNT(*),SUM(字数) FROM 文献块"
                             " GROUP BY source ORDER BY COUNT(*) DESC"):
            print("  %-4s %4d 条  %d 字" % r)

        print("\n=== 抽查：太阳病篇条文1（倪）===")
        r = con.execute("SELECT 篇,标题,字数 FROM 文献块 WHERE 书 LIKE '%太阳病篇%'"
                        " ORDER BY id LIMIT 4").fetchall()
        for x in r:
            print("   %s ｜ %s ｜ %d 字" % x)

        print("\n=== 抽查：关键词挂回方剂/六经 ===")
        r = con.execute("SELECT 书,标题,关键词 FROM 文献块"
                        " WHERE 关键词 LIKE '%桂枝汤%' LIMIT 4").fetchall()
        for x in r:
            print("   %s ｜ %-28s ｜ %s" % (x[0], x[1][:28], x[2]))

        print("\n=== 抽查：古籍 伤寒论 前 5 篇 ===")
        for x in con.execute("SELECT 篇,条号,字数 FROM 文献块 WHERE 书='伤寒论'"
                             " ORDER BY id LIMIT 5"):
            print("   %-20s #%-3s %d 字" % x)
    finally:
        con.close()


if __name__ == "__main__":
    main()
