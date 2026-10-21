# -*- coding: utf-8 -*-
"""重出 `_受控词表.json` —— 从库里的规范因子生成，顺便并掉 6 条"归一当时选错了规范形"的残留

## 为什么要重出（2026-10-14）

面板 `面板.py:63` 读的是 `_受控词表.json`（**10-11 的文件，113 个词**），
而词表归一是 **10-13** 做的，成果落在 `_词表_新.tsv` 和 `医案因子`（**247 个规范因子**）。
**面板喂给模型的还是两天前那份。**

后果是实的：**模型只能从那 113 个里挑标签，另外 134 个因子它永远打不出来** ——
而检索是拿标签去匹配的，**召回在源头上就缺一半**。
（形状跟当天挖出的「文献块没人读」一样：活干完了，没到模型眼前。）

## 为什么要并这 6 条

重出前先看了一遍 247 个：**它不是干净的**，里面有成对的 OCR 变体。
`原词` 列把根因摆出来了 —— **归一当时把 OCR 垃圾映射成了另一个垃圾**：

    颈部僵硬   ← 原词「颈部健观」        口摩口不仁 ← 原词「口腐口不仁」/「口魔口不仁」
    病症反测发作 ← 原词「病症反显发作」     病症退寒加重 ← 原词「病症退寒加通」
    心律不     ← 截断（心律不齐 n=8）
    易忍生气   ← 「易忍」不是词（易生气 n=6）

**注意：这 6 条是人工定的，不是算法跑的。** 同长度、编辑距离 1 的词有 171 对，
其中**绝大多数是合法区分**（手冷/脚冷、背痛/腰痛、沉脉/浮脉）——
所以**不能自动并**（同 `_修词表.py` 的教训：差一个字常常是反义）。
底下 `合并` 表里每一条都得能说出"为什么这两个是同一个东西"。

**故意不并的两条**（拿不准就不并）：
    舌有瘀点(21) / 舌有紫点(1)   —— 瘀→紫 不是形近字，可能是两个意思
    鼻渊(4) / 鼻病(4)            —— 鼻渊是具体病名，鼻病是泛称

幂等：合并（UPDATE 因子 + 保留 原词 + 去重）跑几次结果一样；词表重建是全量覆盖。
"""
import json
import os
import shutil
import sqlite3
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
JSON = os.path.join(HERE, "_受控词表.json")

# 变体 → 规范形。**每一条都要能说出为什么是同一个东西**，说不出的不许进这张表。
合并 = {
    "心律不": "心律不齐",           # n=1 → n=8，截断
    "易忍生气": "易生气",           # n=1 → n=6，「易忍」不成词
    "颈部僵硬": "颈部硬",           # n=1 → n=13，原词就是「颈部健观」=OCR
    "口摩口不仁": "口麻口不仁",      # 原词「口腐/口魔口不仁」；「口摩」不通，麻=麻木
    "病症反测发作": "病症反复发作",   # 原词「病症反显发作」；「反测」不成词
    "病症退寒加重": "病症遇寒加重",   # 原词「病症退寒加通」；「退寒加重」语义不通
}
不并 = {
    "舌有瘀点": "舌有紫点",         # 瘀→紫 不是形近字，可能是两个意思 → 拿不准，不并
    "鼻渊": "鼻病",                # 鼻渊是具体病名，鼻病是泛称 → 不并
}


def main():
    con = sqlite3.connect(DB)
    改 = 0
    for 旧, 新 in 合并.items():
        n = con.execute("SELECT COUNT(*) FROM 医案因子 WHERE 因子=?", (旧,)).fetchone()[0]
        if not n:
            continue
        # 留痕：原词存**第一次的**值（COALESCE），跟 `_修倪药名.py` 同一条纪律
        con.execute("UPDATE 医案因子 SET 原词=COALESCE(原词,因子), 因子=? WHERE 因子=?", (新, 旧))
        改 += n
    # 并完可能撞车（同一个案本来两条都有）→ 去掉重复，保留 id 最小的那条
    con.execute("DELETE FROM 医案因子 WHERE id NOT IN "
                "(SELECT MIN(id) FROM 医案因子 GROUP BY 医案id,维度,因子)")
    con.commit()

    # ── 从库里的规范因子重出词表 ──
    分 = defaultdict(list)
    for 维, 因, n in con.execute(
            "SELECT 维度,因子,COUNT(*) FROM 医案因子 WHERE 维度 IN ('症状','舌脉')"
            " GROUP BY 维度,因子 ORDER BY COUNT(*) DESC, 因子"):
        分[维].append(因)

    if os.path.exists(JSON):
        shutil.copy2(JSON, JSON + ".bak-20261014")

    出 = {"舌脉": 分.get("舌脉", []), "症状": 分.get("症状", [])}
    with open(JSON, "w", encoding="utf-8") as f:
        json.dump(出, f, ensure_ascii=False, indent=1)

    总 = len(出["舌脉"]) + len(出["症状"])
    print("=== 合并（补归一当时的漏）===")
    for 旧, 新 in 合并.items():
        print("  %-12s → %-12s" % (旧, 新))
    print("  改了 %d 行；去重后 医案因子 共 %d 行" % (改, con.execute("SELECT COUNT(*) FROM 医案因子").fetchone()[0]))
    print("\n=== 故意不并 ===")
    for a, b in 不并.items():
        print("  %-8s ／ %-8s  —— 拿不准就不并" % (a, b))
    print("\n=== 新的 _受控词表.json ===")
    print("  舌脉 %d ／ 症状 %d ／ **合计 %d**（原来是 113）" % (len(出["舌脉"]), len(出["症状"]), 总))
    print("  旧文件已备份成 _受控词表.json.bak-20261014")
    con.close()


if __name__ == "__main__":
    main()
