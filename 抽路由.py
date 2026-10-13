# -*- coding: utf-8 -*-
"""⑤ 路由 · T5 场景路由 + T9 关键词路由

T5 场景路由：SKILL.md §核心立场融合（6维度）+ §场景路由代码块（7条）
T9 关键词路由：SKILL.md §关键词→模块细粒度路由表（6张子表）

**T9 的关键**：原文的「模块文件」指向 .md 路径，入库后这些路径会变成死链，
必须改写成「目标表 + 目标id」。所以每行都带 目标表/目标id，原文路径留作溯源。
"""
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
SKILL = r"D:\Download\_tcm_src\TCM-Skill-main\skill\SKILL.md"

经名 = {"01_太阳病注解": "太阳", "02_阳明病注解": "阳明", "03_少阳病注解": "少阳",
        "04_太阴病注解": "太阴", "05_少阴病注解": "少阴", "06_厥阴病注解": "厥阴"}


def 定位(路径):
    """把 'references/xxx.md' 映射到 (目标表, 目标id)"""
    p = 路径.strip("`").strip()
    for k, v in 经名.items():
        if k in p:
            return "六经", v
    if "本草经" in p or "药名索引" in p:
        return "本草", None
    if "针灸处方手册" in p:
        return "针灸处方", None
    if "03_jingfang" in p:
        return "剂量_规范", None
    if ("/cases/" in p or "clinical_records" in p
            or "case_logs" in p or "li_depth/cases" in p):
        return "医案", None
    return "文献块", None          # 金匮诸篇 / ni_depth,li_depth 的 modules


def 抽场景路由():
    out = []
    # --- 融合立场表（6 维度）---
    txt = open(SKILL, encoding="utf-8").read()
    m = re.search(r"###\s*核心立场融合\s*\n(.*?)\n###", txt, re.S)
    if m:
        for 线 in m.group(1).splitlines():
            线 = 线.strip()
            if not 线.startswith("|") or set(线) <= set("|-: "):
                continue
            格 = [c.strip().strip("*") for c in 线.strip("|").split("|")]
            if len(格) < 4 or 格[0] == "维度":
                continue
            out.append((格[0], 格[3], "倪：%s ｜ 李：%s" % (格[1], 格[2]),
                        None, None, "SKILL.md §核心立场融合"))

    # --- 场景路由代码块（7 条，硬编码，树形不适合正则）---
    硬 = [
        ("常规辨证", "倪海厦", "感冒/失眠/胃痛/月经/咳嗽等",
         "六经辨证 → 经方常规剂量 → 针灸配合", "references/modules/ + ni_depth/"),
        ("急危重症", "李可", "心衰/休克/中风昏迷/大出血等",
         "破格救心汤体系，附子30-200g，武火急煎。⚠️必须提醒立即送医", "li_depth/cases/"),
        ("中风急救", "李可+倪海厦", "中风",
         "李可续命煮散（汤药）+ 倪海厦急救七大穴（针灸），两大师承协同",
         "li_depth/cases/02_zhongfeng.md"),
        ("肿瘤/癌症", "倪定位 + 李攻坚", "肿瘤/癌症",
         "倪海厦六经定位（辨证框架）+ 李可攻癌夺命汤（攻坚用药）",
         "li_depth/cases/04_zhongliu.md"),
        ("慢性病善后", "李可", "大病愈后/术后恢复/体质虚弱",
         "培元固本散体系 → 顾肾气保胃气", "li_depth/modules/04_peiyuan.md"),
        ("争议场景", "倪李双方案", "糖尿病/高血压/重度肺炎/风湿/失眠",
         "同时输出倪海厦+李可双方案，标注分歧点和选择原则", None),
        ("药物性味查询", "倪(常规) + 李(峻药剂量)", "查药性",
         "倪海厦本草经345味（常规）；李可经方有效量（附/麻/细/半/吴茱萸等峻药）",
         "references/modules/18~20_本草经_*品注释.md"),
    ]
    for 场景, 用谁, 判据, 提示, 指向 in 硬:
        out.append((场景, 用谁, 判据, 提示, 指向, "SKILL.md §场景路由"))
    return out


def 抽关键词路由():
    txt = open(SKILL, encoding="utf-8").read()
    m = re.search(r"###\s*关键词→模块细粒度路由表\s*\n(.*?)\n\*\*使用优先级\*\*", txt, re.S)
    if not m:
        raise SystemExit("找不到 关键词路由表")
    段 = m.group(1)

    类别 = None
    行 = []
    for 线 in 段.splitlines():
        h = re.match(r"^\*\*(.+?)\*\*", 线.strip())
        if h and "路由" in h.group(1):
            名 = h.group(1)
            for k in ("金匮", "伤寒", "本草", "针灸", "医案", "双师"):
                if k in 名:
                    类别 = k
                    break
            else:
                类别 = 名
            continue
        t = 线.strip()
        if not t.startswith("|") or set(t) <= set("|-: "):
            continue
        格 = [c.strip().strip("`").strip() for c in t.strip("|").split("|")]
        if len(格) < 3 or 格[0] in ("关键词", "场景") or not 类别:
            continue
        关键词 = 格[0].replace("**", "").strip()
        路径 = 格[1].strip()
        目标表, 目标id = 定位(路径)
        行.append((关键词, 类别, 目标表, 目标id, 路径,
                   "SKILL.md §关键词→模块细粒度路由表"))
    return 行


def main():
    con = sqlite3.connect(DB)
    try:
        T5 = 抽场景路由()
        con.execute("DELETE FROM 场景路由")
        con.executemany("INSERT INTO 场景路由(场景,用谁,判据,提示,指向,出处)"
                        " VALUES(?,?,?,?,?,?)", T5)

        T9 = 抽关键词路由()
        con.execute("DELETE FROM 关键词路由")
        con.executemany("INSERT INTO 关键词路由(关键词,类别,目标表,目标id,原文路径,出处)"
                        " VALUES(?,?,?,?,?,?)", T9)
        con.commit()

        print("  场景路由 %d 条 ｜ 关键词路由 %d 条" % (len(T5), len(T9)))
        print("\n=== 关键词路由 · 按目标表统计（这就是「改写指向」的结果）===")
        for r in con.execute("SELECT 目标表,COUNT(*) FROM 关键词路由 GROUP BY 目标表"
                             " ORDER BY COUNT(*) DESC"):
            print("  %-8s %d 条" % r)
        print("\n=== 关键词路由 · 按类别 ===")
        for r in con.execute("SELECT 类别,COUNT(*) FROM 关键词路由 GROUP BY 类别"
                             " ORDER BY COUNT(*) DESC"):
            print("  %-6s %d 条" % r)
        print("\n抽查（伤寒那批应指向 六经 且 id 是经名）：")
        for r in con.execute("SELECT 关键词,目标表,目标id FROM 关键词路由"
                             " WHERE 类别='伤寒'"):
            print("  %-34s → %s(%s)" % r)
        print("\n抽查（本草那批应指向 本草 表）：")
        for r in con.execute("SELECT 关键词,目标表 FROM 关键词路由 WHERE 类别='本草'"):
            print("  %-34s → %s" % r)
    finally:
        con.close()


if __name__ == "__main__":
    main()
