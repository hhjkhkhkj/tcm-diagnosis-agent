# -*- coding: utf-8 -*-
"""重归标签 —— 把两侧医案的症状/舌脉按新词表归一，写进 T18 `医案因子`

**只加不删**：`医案.症状/舌脉` 原列一个字不动（`检索.py` 还在读它），
归一结果另存新表。每行都留 `原词`，归一推倒重来不用重调模型。

顺带修两件老毛病：
  ① 症状/舌脉 边界是坏的 —— 倪案「两尺弱」落在症状列，问止「细脉/数脉」也落在症状列。
     这里**不再信任原列名**，按规范词判定维度（含 舌/脉/苔/尺/寸/关 的一律算舌脉）。
  ② 连写的多标签拆开（表格列结构在 OCR 里丢了）。

来源怎么标（供第二阶段审计用，别混）：
  原文      问止那 50 例 —— 是 `_抽标签.py` 从 OCR「辨证论治」块**逐行摘的原文**
  模型抽取  倪案 951 例 —— 是 `_抽倪标签.py` 让模型从倪氏原文里**认出来的**（忠实摘录）
  （以后抽六经/体质/治法时，模型**推断**出来的标 `模型推断`，那批不能当结果变量用）

用法：py _重归标签.py
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opencc import OpenCC                      # noqa: E402
import 词表修订 as V                          # noqa: E402

SRC = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SRC, "中医大脑医案库.db")
简 = OpenCC("t2s")

RE_洗 = re.compile(r"[\s，,、；;：:·．.－\-–—／/【】\[\]()（）]+")
RE_舌脉 = re.compile(r"[舌脉苔尺寸关]")

来源表 = {"问止": "原文", "倪海厦": "模型抽取"}


def 洗(s):
    return RE_洗.sub("", 简.convert(s or ""))


def 载别名():
    """变体 → 规范词（含规范词自身的恒等映射）；顺带带回全书频次"""
    m, 频 = {}, {}
    fp = os.path.join(SRC, "_词表_新.tsv")
    for i, ln in enumerate(open(fp, encoding="utf-8")):
        if i == 0:
            continue
        p = ln.rstrip("\n").split("\t")
        if len(p) < 4:
            continue
        头 = p[0].strip()
        if not 头:
            continue
        try:
            频[头] = int(p[1])
        except ValueError:
            频[头] = 0
        m[头] = 头
        for a in p[3].split(" / "):
            a = a.strip()
            if a:
                m.setdefault(a, 头)
    return m, 频


别名到规范, 全书频 = 载别名()


def 归一(原, 深=0):
    """一个原词 → 零个或多个规范词"""
    出 = []
    if 深 > 3:            # 防拆到自己（"肺气虚少气懒言"→它自己 那种）
        return 出
    t = 洗(原)
    if not t:
        return 出
    if t in V.连写待拆:
        for x in V.连写待拆[t]:
            出.extend(归一(x, 深 + 1))
        return 出 or [t]
    if t in V.乱码映射:
        t = V.乱码映射[t]
    状态, _ = V.查状态(t)
    if 状态 == "删":
        return 出
    t = 别名到规范.get(t, t)
    t = 洗(t or "")
    if t:
        if t not in 出:
            出.append(t)
    return 出


def 判维度(词):
    return "舌脉" if RE_舌脉.search(词) else "症状"


def main():
    if not os.path.exists(DB):
        print("找不到库：%s" % DB)
        return
    con = sqlite3.connect(DB)

    con.execute("""CREATE TABLE IF NOT EXISTS 医案因子(
        id INTEGER PRIMARY KEY,
        医案id INTEGER, 维度 TEXT, 因子 TEXT, 原词 TEXT, 来源 TEXT,
        证据 TEXT, 置信度 REAL)""")
    con.execute("DELETE FROM 医案因子 WHERE 维度 IN ('症状','舌脉')")

    行 = []
    统计 = defaultdict(Counter)
    未命中 = Counter()
    挪列 = 0

    for 医案id, src, 症状, 舌脉 in con.execute(
            "SELECT id,source,症状,舌脉 FROM 医案 WHERE source IN ('问止','倪海厦')"):
        来源 = 来源表.get(src, "原文")
        for 列名, 原串 in (("症状", 症状), ("舌脉", 舌脉)):
            try:
                arr = json.loads(原串 or "[]")
            except Exception:
                arr = []
            for 原 in arr:
                词表 = 归一(原)
                统计[src]["归一前"] += 1
                if not 词表:
                    统计[src]["丢弃"] += 1
                    未命中[洗(原)] += 1
                    continue
                统计[src]["归一后"] += len(词表)
                原词 = 洗(原)
                for 词 in 词表:
                    维 = 判维度(词)
                    if 维 != 列名:
                        挪列 += 1
                    行.append((医案id, 维, 词, 原词, 来源, 原, 1.0))

    con.executemany("INSERT INTO 医案因子(医案id,维度,因子,原词,来源,证据,置信度)"
                    " VALUES(?,?,?,?,?,?,?)", 行)
    con.execute("CREATE INDEX IF NOT EXISTS ix_因子_案 ON 医案因子(医案id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_因子_词 ON 医案因子(因子)")
    con.commit()

    总 = con.execute("SELECT COUNT(*) FROM 医案因子").fetchone()[0]
    print("写进 `医案因子` %d 行" % 总)
    for src, c in 统计.items():
        print("  %-8s 归一前 %d → 归一后 %d，丢弃 %d"
              % (src, c["归一前"], c["归一后"], c["丢弃"]))
    print("按规范词重判维度、从症状挪到舌脉的：%d 条" % 挪列)

    词频 = Counter(r[0] for r in con.execute("SELECT 因子 FROM 医案因子"))
    print("\n归一后 distinct 因子：%d 个" % len(词频))
    print("  出现 >=5 次的：%d 个（第二阶段建模的可用面）"
          % len([w for w, c in 词频.items() if c >= 5]))
    print("\n=== 归一后前 25 高频 ===")
    for w, c in 词频.most_common(25):
        print("  %-20s %d" % (w, c))

    with open(os.path.join(SRC, "_归一报告.md"), "w", encoding="utf-8") as f:
        f.write("# 归一报告\n\n")
        f.write("| 源 | 归一前 | 归一后 | 丢弃 |\n|---|---|---|---|\n")
        for src, c in 统计.items():
            f.write("| %s | %d | %d | %d |\n" % (src, c["归一前"], c["归一后"], c["丢弃"]))
        f.write("\n按规范词重判维度、从症状挪到舌脉的：%d 条\n\n" % 挪列)
        f.write("归一后 distinct 因子 **%d** 个，其中出现 ≥5 次的 **%d** 个。\n\n"
                % (len(词频), len([w for w, c in 词频.items() if c >= 5])))
        f.write("## 丢弃的词\n\n" + "、".join("%s(%d)" % (w, c) for w, c in 未命中.most_common()) + "\n")
        复 = [(w, c) for w, c in 未命中.items() if c >= 3]
        f.write("\n## ⚠️ 复核：被丢掉的词里，见于 ≥3 个案子的（%d 个）\n\n"
                "**这几个必须回头看** —— 出现在三个以上案子里还判成垃圾，很可能是我误删。\n"
                "（「周口太好」就是这么被抓出来的：见于 13 案，实为「胃口太好」。）\n"
                "判据用「**见于几案**」而不是「全书出现几次」—— 后者会把「小便」这种\n"
                "在散文和「【小便】」标题里出现 307 次的词误报出来。\n\n" % len(复))
        for w, c in sorted(复, key=lambda x: -x[1]):
            f.write("- `%s` 见于 %d 案\n" % (w, c))
    if 复:
        print("\n⚠️ 复核：被丢掉的词里见于 ≥3 案的 %d 个 —— %s"
              % (len(复), "、".join("%s(%d案)" % (w, c) for w, c in sorted(复, key=lambda x: -x[1]))))
    print("\n写了一份 _归一报告.md")
    con.close()


if __name__ == "__main__":
    main()
