# -*- coding: utf-8 -*-
"""文献块 · 补第二批（2026-10-14）

## 为什么有这一批

`_抽文献块.py` 的 docstring 写着「**只灌高频篇，不整吞**」，并**有意排除**了
倪的 4 个 + 古籍的 12 部（理由：本草已由 T12 覆盖、金鉴与伤寒金匮重复）。

**但那个清单里没有 `cases/` —— 它压根没扫到那些子目录。**
漏的是这四批：

    references/modules/       22 文件  19,328 行   六经注解/金匮注解/本草经注释/针灸手册
    references/cases/          6 文件   1,616 行   医宗金鉴里提取的医案
    references/li_depth/cases/ 5 文件   5,739 行   **李可专病医案**（心衰/中风/肺病/肿瘤）
    references/ni_depth/cases/ 6 文件   2,040 行   倪案按病分类

**外加古籍剩的 12 部**（医宗金鉴 3.4万行 / 针灸大成 / 外科正宗 / 肘后备急方 /
本草备要 / 本草正义 / 神农本草经 / 脉经 / 针灸甲乙经 / 傅青主女科 / 难经 / 濒湖脉学）。

## ⚠️ 顺便统一出处的写法（这是「关键词路由」死链的根因）

    路由的原文路径：`references/modules/12_金匮_肺病奔豚胸痹.md`   ← 全路径
    已入库的出处：  `ni_depth/modules/01_shanghan_sun.md#L14`        ← **剥了 references/**

**两边格式不一致，所以 66 条路由里 59 条对不上。**
本脚本统一成**剥掉 `references/`** 的写法（跟已入库的 769 块一致），
并把路由的 `原文路径` 也改成同一格式，最后**回填 `目标id`**。

## 幂等

先删 `source IN ('注解','医源','古籍')` 里**这批文件对应的**，再插（按出处前缀判）。
"""
import glob
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
基 = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references"

# 这批要入的目录（source, 书名前缀怎么取）
目录 = [
    ("modules/*.md", "注解", "注解·"),
    ("cases/*.md", "医源", "医案·"),
    ("li_depth/cases/*.md", "医源", "李可·"),
    ("ni_depth/cases/*.md", "医源", "倪海厦·"),
    ("古籍/*.md", "古籍", ""),
    # 这几个原脚本**有意排除**过（理由：医案已由 T13 覆盖 / 内经已从古籍入 / 低频）。
    # 但排除的理由是"别处有"，不是"没价值" —— 它们本身是倪的讲解，检索用得上。
    # 已入过的会自动跳过，这里补的是 03_yian / 05_huangdi_neijing / 06_liangdong / 07 / 08
    ("ni_depth/modules/*.md", "ni", "倪海厦·"),
    # 诊疗日志（汉唐中医网站文章汇集）—— 叙述性文章，跟结构化医案不同源
    ("ni_depth/case_logs/*.md", "ni", "倪海厦·诊疗日志"),
]


def 出处路径(fp):
    """相对 references/ 的路径（**剥掉 references/**，跟已入库的写法对齐）"""
    return os.path.relpath(fp, 基).replace("\\", "/")


def 切块(文, 上限=6000):
    """按 ## 切；没有 ## 就按 ### 切。**切完仍超 6000 字的按空行再切**
    （原 `_抽文献块.py` 就是这么做的 —— 不补这步会切出 6 万字一块的怪物）"""
    行 = 文.split("\n")
    出, 当标, 当文 = [], None, []
    for ln in 行:
        if re.match(r"^#{2,3}\s+\S", ln):
            if 当文 and "".join(当文).strip():
                出.append((当标 or "", "\n".join(当文).strip()))
            当标 = re.sub(r"^#+\s*", "", ln).strip()
            当文 = []
        else:
            当文.append(ln)
    if 当文 and "".join(当文).strip():
        出.append((当标 or "", "\n".join(当文).strip()))
    if not 出 and 文.strip():
        出 = [("", 文.strip())]

    # 再切：太长的按空行断开（要的是可检索的块，不是一整篇）
    细 = []
    for 标, 正文 in 出:
        if len(正文) <= 上限:
            细.append((标, 正文))
            continue
        段, 长 = [], 0
        for p in 正文.split("\n\n"):
            if 长 + len(p) > 上限 and 段:
                细.append((标, "\n\n".join(段)))
                段, 长 = [], 0
            段.append(p)
            长 += len(p)
        if 段:
            细.append((标, "\n\n".join(段)))
    return 细


def 关键词(文, 六经, 方名):
    出 = [x for x in 六经 if x in 文]
    出 += [x for x in 方名 if x in 文]
    return json.dumps(sorted(set(出)), ensure_ascii=False)


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    六经 = [r[0] for r in con.execute("SELECT 经 FROM 六经")]
    方名 = [r[0] for r in con.execute("SELECT DISTINCT 方名 FROM 方剂 WHERE 方名 IS NOT NULL")]

    # 已入库的出处（判断哪些文件入过了）
    已有 = set()
    for (出,) in con.execute("SELECT DISTINCT 出处 FROM 文献块 WHERE 出处 IS NOT NULL"):
        已有.add(re.sub(r"#.*$", "", 出).strip())

    源集 = {}
    for 模式, src, 前缀 in 目录:
        for fp in sorted(glob.glob(os.path.join(基, 模式))):
            源集[fp] = (src, 前缀)

    print("扫到 %d 个文件" % len(源集))
    新, 跳, 块数 = [], 0, 0
    for fp, (src, 前缀) in 源集.items():
        路 = 出处路径(fp)
        if 路 in 已有:          # 这个文件已经入过 → 跳过（幂等 + 不重复入）
            跳 += 1
            continue
        文 = open(fp, encoding="utf-8", errors="ignore").read()
        篇 = os.path.basename(fp).replace(".md", "")
        书 = 前缀 + 篇
        for 标, 正文 in 切块(文):
            if len(正文) < 20:     # 太短的不是块，是残句
                continue
            块数 += 1
            新.append((src, 书, 篇, "", 标, 正文, len(正文),
                       关键词(正文, 六经, 方名), 路))

    print("  已入过、跳过 %d 个文件" % 跳)
    print("  新切出 %d 块" % 块数)
    if 新:
        con.executemany(
            "INSERT INTO 文献块(source,书,篇,条号,标题,正文,字数,关键词,出处)"
            " VALUES(?,?,?,?,?,?,?,?,?)", 新)
        con.commit()
    tot = con.execute("SELECT COUNT(*) FROM 文献块").fetchone()[0]
    print("  文献块 现在 %d 块" % tot)

    # ── 统一路由的原文路径 + 回填目标id ──
    改 = 0
    for id_, 路 in con.execute("SELECT id,原文路径 FROM 关键词路由 WHERE 原文路径 IS NOT NULL"):
        新路 = re.sub(r"^references/", "", 路.strip()).split("+")[0].strip()
        if 新路 != 路:
            con.execute("UPDATE 关键词路由 SET 原文路径=? WHERE id=?", (新路, id_))
            改 += 1
    con.commit()
    print("\n路由：统一出处格式 %d 条" % 改)

    回 = 0
    for id_, 路 in con.execute("SELECT id,原文路径 FROM 关键词路由 WHERE 原文路径 IS NOT NULL"):
        前缀 = re.sub(r"#.*$", "", 路)
        r = con.execute("SELECT id FROM 文献块 WHERE 出处 LIKE ? LIMIT 1",
                        (前缀 + "%",)).fetchone()
        if r:
            con.execute("UPDATE 关键词路由 SET 目标id=? WHERE id=?", (r[0], id_))
            回 += 1
    con.commit()
    空 = con.execute("SELECT COUNT(*) FROM 关键词路由 WHERE 目标id IS NULL").fetchone()[0]
    print("路由：回填目标id %d 条，仍为空 %d 条" % (回, 空))

    # ── 收尾：修 typo + 处理「指向目录」的两条 ──
    con.execute("UPDATE 关键词路由 SET 原文路径=? WHERE 原文路径 LIKE ?",
                ("li_depth/cases/04_zhongliu.md", "%04_zhongliu.md`%"))
    con.commit()
    修2 = 0
    for id_, 路 in list(con.execute(
            "SELECT id,原文路径 FROM 关键词路由 WHERE 目标id IS NULL")):
        前缀 = re.sub(r"#.*$", "", 路 or "").rstrip("/")
        # ⚠️ 出处/源id 有两种写法：带 ni_depth/ 的和不带的 —— **两种都要试**
        #    （不带的那次兜底查曾经漏掉 clinical_records，因为源id 是 `clinical_records_...`）
        试 = [前缀] + ([前缀.split("/", 1)[1]] if "/" in 前缀 else [])
        r = None
        for p in 试:
            r = (con.execute("SELECT id FROM 医案 WHERE 源id LIKE ? LIMIT 1", (p + "%",)).fetchone()
                 or con.execute("SELECT id FROM 文献块 WHERE 出处 LIKE ? LIMIT 1", (p + "%",)).fetchone())
            if r:
                break
        if r:
            con.execute("UPDATE 关键词路由 SET 目标id=? WHERE id=?", (r[0], id_))
            修2 += 1
    con.commit()
    空 = con.execute("SELECT COUNT(*) FROM 关键词路由 WHERE 目标id IS NULL").fetchone()[0]
    print("收尾：又补了 %d 条；**最终仍为空 %d 条**" % (修2, 空))
    con.close()


if __name__ == "__main__":
    main()
