# -*- coding: utf-8 -*-
"""抽 彭子益《圆运动的古中医学》古方 → T17 李可验方（source='彭子益'）

## 为什么补这一批（2026-10-14 守拙点出）

2026-10-13 抽李可验方时，我把 `03_jingfang.md` §十一 整段排除了，
理由写的是「**彭子益圆运动古方（41 首，另一作者）**」。

**这个理由不成立。** 原文自己写着：

    > 来源：彭子益《圆运动的古中医学》，**李可主校**（中医药出版社2007年）

李可在 `02_shanghan.md` 里称彭子益「**中医复兴之父**」
「**继医圣张仲景之后第二位中医中圣人**」。李可那套「阳气圆运动」的底子
就是从这儿来的 —— 排除这一段，等于把李可的一条根砍掉。

**教训**：判断"这条要不要入"时，我去看的是**署名**（彭子益 ≠ 李可），
而没去看**关系**（李可主校、李可推崇、李可借用其法）。
署名不同 ≠ 与这个人无关。库里凡是「李可转述/李可主校/李可引用」的段落，
都该按李可的资料对待。

## 形态

    ## 十一、彭子益圆运动古方（《圆运动的古中医学》）
    ### 11.1 内伤病六方（运轴行轮之法）
    #### 11.1.1 理中丸（彭子益方·治中土不运）
    **组成**：人参（党参）、白术各二钱，干姜、炙甘草各一钱
    **主治**：夏月寒霍乱，…脉象虚大或微小
    **方解**：中气虚寒，土气湿寒。…此运轴行轮之法
    **用法**：古法煎药只煎一次，分作三服

## ⚠️ 两处跟李可那批不一样，别照抄 `_抽验方.py`

1. **方在 `####` 一级**（李可那批在 `###`），而 `###` 在这里是"篇"不是方
2. **11.3~11.6 按病种分（温病本气篇／儿病本气篇／圆运动续集），不按六经** ——
   那几篇的 `六经` 只能是 NULL。**这是分类方式不同，不是抽取失败**，
   别把它当 bug 去"修"（当成 bug 就会去硬凑一个六经，那才是污染）

幂等：按 `source='彭子益'` 先删后插，**不动 source='李可' 那 115 首**。
"""
import os
import re
import sqlite3
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
JINGFANG = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references\li_depth\modules\03_jingfang.md"

六经名 = ["太阳", "阳明", "少阳", "太阴", "少阴", "厥阴"]
字段re = re.compile(r"^\*\*(组成|主治|方解|用法|医案)\*\*\s*[：:]\s*(.*)$")
方re = re.compile(r"^####\s+\S*\s*(.+?)\s*$")      # 方在 ####
篇re = re.compile(r"^###\s+\S*\s*(.+?)\s*$")        # 篇在 ###


def 取本段(文本):
    """只取 §十一「彭子益圆运动古方」这一段（到下一个 ## 为止）"""
    行 = 文本.splitlines()
    起 = None
    for i, 线 in enumerate(行):
        if 线.startswith("## ") and "彭子益" in 线:
            起 = i
            break
    if 起 is None:
        return []
    for j in range(起 + 1, len(行)):
        if 行[j].startswith("## "):
            return 行[起 + 1:j]
    return 行[起 + 1:]


def 解析(行列表):
    """→ [(篇, 方名附注, {字段: 值})]"""
    出, 篇, 方名, 字段, 当前 = [], "", None, {}, None
    for 线 in 行列表:
        m = 方re.match(线)
        if m:
            if 方名:
                出.append((篇, 方名, 字段))
            方名, 字段, 当前 = m.group(1), {}, None
            continue
        m = 篇re.match(线)
        if m and not 线.startswith("####"):
            if 方名:                       # 篇换了一节，收尾
                出.append((篇, 方名, 字段))
                方名, 字段, 当前 = None, {}, None
            篇 = m.group(1)
            continue
        if 方名 is None:
            continue
        f = 字段re.match(线)
        if f:
            当前 = f.group(1)
            字段[当前] = f.group(2).strip()
            continue
        if not 线.strip() or 线.startswith(("**", "#", ">")):
            当前 = None
            continue
        if 当前:                            # 续行
            字段[当前] += 线.strip()
    if 方名:
        出.append((篇, 方名, 字段))
    return 出


def 判六经(方名附注, 主治):
    """六经只能从「标题括注 + 主治」里认，认不出就是 NULL —— 不猜"""
    t = (方名附注 or "") + " " + (主治 or "")
    hit = [k for k in 六经名 if k in t]
    return hit[0] if hit else None


def main():
    行 = 取本段(open(JINGFANG, encoding="utf-8").read())
    if not 行:
        raise SystemExit("没找到 §十一 彭子益段 —— 源文件改了？先看一眼再跑")

    数据 = []
    for 篇, 方名附注, 字段 in 解析(行):
        组成 = (字段.get("组成") or "").strip()
        if not 组成:
            continue                        # 光有标题没组成的（如"厥阴提纲"那类），跳过
        方名 = re.sub(r"（.*?）", "", 方名附注).strip()      # 去掉「（彭子益方·治…）」
        附注 = (re.search(r"（(.*?)）", 方名附注) or [None, ""])[1]
        数据.append((判六经(附注 + 方名附注, 字段.get("主治")),
                     方名, 组成, 字段.get("主治"), 字段.get("方解"),
                     字段.get("用法"), 字段.get("医案"),
                     "03_jingfang.md 十一、彭子益圆运动古方 · %s ｜ %s" % (篇, 附注),
                     "彭子益"))

    con = sqlite3.connect(DB)
    try:
        旧 = con.execute("SELECT COUNT(*) FROM 李可验方 WHERE source='彭子益'").fetchone()[0]
        con.execute("DELETE FROM 李可验方 WHERE source='彭子益'")
        con.executemany(
            "INSERT INTO 李可验方(六经,方名,组成,主治,方解,用法,医案,出处,source)"
            " VALUES(?,?,?,?,?,?,?,?,?)", 数据)
        con.commit()

        print("  旧 %d 行 → 新入 %d 首" % (旧, len(数据)))
        print("  按六经：%s" % dict(Counter(d[0] or "（不按六经分）" for d in 数据)))
        print("  带方解 %d ／ 带用法 %d ／ 带主治 %d"
              % (sum(1 for d in 数据 if d[4]), sum(1 for d in 数据 if d[5]),
                 sum(1 for d in 数据 if d[3])))
        全 = con.execute("SELECT source,COUNT(*) FROM 李可验方 GROUP BY source").fetchall()
        print("  T17 现在：%s" % dict(全))
        print("\n抽查（每篇两首）：")
        for 篇 in ("内伤病六方", "外感病十方", "温病本气篇", "儿病本气篇"):
            for 名, 组, 治 in con.execute(
                    "SELECT 方名,组成,主治 FROM 李可验方 WHERE source='彭子益'"
                    " AND 出处 LIKE ? LIMIT 2", ("%" + 篇 + "%",)):
                print("  [%s] %-12s %s" % (篇, 名, (组 or "")[:40]))
                print("      %s" % (治 or "")[:46])
    finally:
        con.close()


if __name__ == "__main__":
    main()
