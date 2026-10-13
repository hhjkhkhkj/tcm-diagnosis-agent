# -*- coding: utf-8 -*-
"""
抽主干层 T1 / T2 / T4 / T6 / T7 / T8 —— 源：SKILL.md（倪海厦 + 李可）

确定性抽取：**原文本身就是表**，不调模型、不做概括。
每一条都带 出处（章节名 + 行号），可回原文件逐条核对。

用法：py _抽主干.py
"""
import json
import re
import sqlite3

SKILL = r"D:\Download\_tcm_src\TCM-Skill-main\skill\SKILL.md"
DB = r"D:\Download\医案集_OCR\中医大脑医案库.db"


# ---------- markdown 解析 ----------

def 读行():
    return open(SKILL, encoding="utf-8").read().splitlines()


def 找章节(行, 标题, 级=None):
    """返回 [起, 止) 行号。级=None 时自动按标题的 # 个数判定边界。"""
    起 = None
    for i, l in enumerate(行):
        if l.strip() == 标题 or l.strip().startswith(标题):
            起 = i
            break
    if 起 is None:
        raise KeyError("找不到章节：%s" % 标题)
    if 级 is None:
        级 = len(行[起]) - len(行[起].lstrip("#"))
    止 = len(行)
    for j in range(起 + 1, len(行)):
        l = 行[j]
        if l.startswith("#"):
            这级 = len(l) - len(l.lstrip("#"))
            if 这级 <= 级:
                止 = j
                break
    return 起, 止


def 抽表格(行, a, b):
    """抽 [a,b) 里所有 markdown 表格。
    返回 [{'头': [...], '行': [[...]], '行号': 首行行号}]"""
    块s = []
    i = a
    while i < b:
        if 行[i].lstrip().startswith("|"):
            首 = i
            原 = []
            while i < b and 行[i].lstrip().startswith("|"):
                原.append(行[i].strip())
                i += 1
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in 原]
            # 去掉 |---|---| 分隔行
            cells = [c for c in cells if not all(re.fullmatch(r":?-{2,}:?", x or "-") for x in c)]
            if len(cells) >= 2:
                块s.append({"头": cells[0], "行": cells[1:], "行号": 首 + 1})
        else:
            i += 1
    return 块s


def 去星(s):
    return (s or "").replace("**", "").replace("`", "").strip()


def 拆判定(s):
    """'少阴病（非太阳！）' → ('少阴病', '非太阳！')；'太阳病' → ('太阳病','')"""
    s = 去星(s)
    m = re.match(r"^([^（(]+)[（(](.+?)[）)]\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


# ---------- 各表 ----------

def 抽六经(行):
    a, b = 找章节(行, "### 六经速查表")
    t = 抽表格(行, a, b)[0]
    out = []
    for r in t["行"]:
        经, 病位, 主证, 主方, 要点 = (r + [""] * 5)[:5]
        out.append((去星(经), 去星(病位), 去星(主证), 去星(主方), 去星(要点),
                    "ni_li", "SKILL.md#六经速查表:%d" % t["行号"]))
    return out


def 抽六经公式(行):
    a, b = 找章节(行, "### 六经辨证诊断公式")
    t = 抽表格(行, a, b)[0]
    出 = "SKILL.md#六经辨证诊断公式:%d" % t["行号"]
    out = []
    for r in t["行"]:
        式, 判 = (r + ["", ""])[:2]
        症状 = [去星(x) for x in re.split(r"[+＋]", 去星(式)) if 去星(x)]
        判定, 排除 = 拆判定(判)
        类型 = "鉴别" if 排除 else "主证"
        out.append((类型, json.dumps(症状, ensure_ascii=False), 判定, 排除, 出))
    return out


def 抽合病(行):
    a, b = 找章节(行, "### 合病与并病")
    表s = 抽表格(行, a, b)
    out = []
    for t in 表s:
        if "治则" in t["头"]:                      # 第一张：类型/含义/治则
            for r in t["行"]:
                类型, 含义, 治则 = (r + [""] * 3)[:3]
                out.append((去星(类型), 去星(含义), 去星(治则), "", "",
                            "SKILL.md#合病与并病:%d" % t["行号"]))
        elif "症状特征" in t["头"]:                 # 第二张：合病类型/症状特征/方剂
            for r in t["行"]:
                名, 症, 方 = (r + [""] * 3)[:3]
                out.append(("合病", "", "先治主证",
                            去星(症), 去星(方),
                            "SKILL.md#合病与并病:%d(%s)" % (t["行号"], 去星(名))))
    return out


def 抽安全规则(行):
    a, b = 找章节(行, "### 安全边界")
    t = 抽表格(行, a, b)[0]
    out = []
    for r in t["行"]:
        规则, 倪, 李 = (r + [""] * 3)[:3]
        # 硬性：双师都反复强调的底线里，「急症送医」是唯一无条件硬性的
        硬性 = "是" if "送医" in 去星(规则) else "否"
        out.append((去星(规则), 去星(倪), 去星(李), 硬性,
                    "SKILL.md#安全边界:%d" % t["行号"]))
    return out


def 抽双师对照(行):
    a, b = 找章节(行, "### 双师对照")
    out = []
    表s = 抽表格(行, a, b)
    组名 = None
    for t in 表s:
        # 往上找最近的 #### 小标题当组名
        for j in range(t["行号"] - 1, a - 1, -1):
            if 行[j].startswith("####"):
                组名 = 去星(行[j].lstrip("#"))
                break
        组号 = (组名 or "").split("：")[0] or "对照"
        for r in t["行"]:
            维度, 倪, 李 = (r + [""] * 3)[:3]
            n, l = 去星(倪), 去星(李)
            同异 = "同" if (n and n == l) else "异"
            out.append((组号, 组名, 去星(维度), n, l, 同异,
                        "SKILL.md#双师对照:%d" % t["行号"]))
    return out


# T8 原文是流程图不是表 → 按 SKILL.md 1065-1088 逐字录
剂量决策 = [
    ("轻证", "体质尚可 / 病程短 / 正气未衰", "倪海厦经方常规剂量",
     "桂枝三钱 / 白芍三钱 / 附子一枚；中病即止，不伤正气",
     "", "SKILL.md#剂量选择决策树:1070-1073"),
    ("中证", "体质偏弱 / 病程较长 / 正气有损", "中等剂量（倪师上限 ≈ 李可下限）",
     "桂枝45g / 白芍45g / 附子15-30g",
     "参考：倪师「桂枝汤可当补药吃」+ 李可「现代人体质多虚，附子的毒性正是救命的仙丹」",
     "SKILL.md#剂量选择决策树:1075-1078"),
    ("重证", "正气大衰 / 西医放弃 / 生死关头", "李可破格大剂量",
     "附子30-200g / 山萸肉60-120g / 麝香0.5g",
     "① 炙甘草 ≥ 附子量（常规1/2~等量，急救 ≥ 等量）；"
     "② 武火急煎 ≥ 1h（分解附子毒性）；"
     "③ 逐日叠加10g观察反应；"
     "④ 黑小豆30g + 蜂蜜150ml解毒配伍；"
     "⑤ 密切观察瞑眩反应",
     "SKILL.md#剂量选择决策树:1080-1088"),
]


# ---------- 入库 ----------

落库 = [
    ("六经", "DELETE FROM 六经", "INSERT INTO 六经(经,病位,主证,主方,要点,source,出处)"
                            " VALUES(?,?,?,?,?,?,?)", 抽六经),
    ("六经公式", "DELETE FROM 六经公式", "INSERT INTO 六经公式(类型,症状组,判定,排除,出处)"
                                    " VALUES(?,?,?,?,?)", 抽六经公式),
    ("合病", "DELETE FROM 合病", "INSERT INTO 合病(类型,含义,治则,症状特征,方剂,出处)"
                              " VALUES(?,?,?,?,?,?)", 抽合病),
    ("安全规则", "DELETE FROM 安全规则", "INSERT INTO 安全规则(规则,倪原话,李原话,硬性,出处)"
                                    " VALUES(?,?,?,?,?)", 抽安全规则),
    ("双师对照", "DELETE FROM 双师对照", "INSERT INTO 双师对照(组号,组名,维度,倪,李,同异,出处)"
                                    " VALUES(?,?,?,?,?,?,?)", 抽双师对照),
]


def main():
    行 = 读行()
    con = sqlite3.connect(DB)
    try:
        for 名, 清, 插, fn in 落库:
            data = fn(行)
            con.execute(清)                      # 幂等：重跑先清本表
            con.executemany(插, data)
            print("  %-8s %d 条" % (名, len(data)))
        con.execute("DELETE FROM 剂量决策")
        con.executemany("INSERT INTO 剂量决策(层级,判据,走谁,示例,须遵守,出处)"
                        " VALUES(?,?,?,?,?,?)", 剂量决策)
        print("  %-8s %d 条" % ("剂量决策", len(剂量决策)))
        con.commit()

        print("\n抽查：")
        r = con.execute("SELECT 类型,症状组,判定,排除 FROM 六经公式 WHERE 类型='鉴别'").fetchall()
        for x in r:
            print("  六经公式·鉴别 → 症状%s → 判定%s（%s）" % (x[1], x[2], x[3]))
        r = con.execute("SELECT 经,主方 FROM 六经").fetchall()
        print("  六经 6 行：%s" % "、".join(a for a, _ in r))
        r = con.execute("SELECT COUNT(*) FROM 双师对照 WHERE 同异='异'").fetchone()[0]
        print("  双师对照中判为「异」的维度：%d 条" % r)
    finally:
        con.close()


if __name__ == "__main__":
    main()
