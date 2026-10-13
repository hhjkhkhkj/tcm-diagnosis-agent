# -*- coding: utf-8 -*-
"""抽 李可六经验方 → T17 李可验方

源：li_depth/modules/03_jingfang.md 的 §八-§十（太阳/阳明/少阳/太阴/少阴/厥阴 六段验方）
每条形如：
    ### 8.1 桂枝葛根止痉散
    **组成**：…
    **主治**：…
    **方解**：…
    **用法**：…
    **医案**：…
「厥阴提纲」这类小节没有 **组成**，自动跳过。
幂等：按 source='李可' 先删后插。
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
方名re = re.compile(r"^###\s+\S+\s+(.+?)\s*$")


def 切段(文本):
    """按 ## 二级标题切段 -> [(标题, 正文行列表)]"""
    段, 标题, 行 = [], None, []
    for 线 in 文本.splitlines():
        if 线.startswith("## "):
            if 标题 is not None:
                段.append((标题, 行))
            标题, 行 = 线[3:].strip(), []
        elif 标题 is not None:
            行.append(线)
    if 标题 is not None:
        段.append((标题, 行))
    return 段


def 解析(行列表):
    """一段内抽方 -> [(方名, {字段: 值})]"""
    方, 方名, 字段, 当前 = [], None, {}, None
    for 线 in 行列表:
        m = 方名re.match(线)
        if m:
            if 方名:
                方.append((方名, 字段))
            方名, 字段, 当前 = m.group(1), {}, None
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
        if 当前:                       # 续行
            字段[当前] += 线.strip()
    if 方名:
        方.append((方名, 字段))
    return 方


def main():
    文本 = open(JINGFANG, encoding="utf-8").read()
    数据 = []
    for 标题, 行列表 in 切段(文本):
        if "验方" not in 标题:
            continue
        经 = next((k for k in 六经名 if k in 标题), None)
        if not 经:
            continue
        for 方名, 字段 in 解析(行列表):
            组成 = (字段.get("组成") or "").strip()
            if not 组成:
                continue
            数据.append((经, 方名, 组成, 字段.get("主治"), 字段.get("方解"),
                         字段.get("用法"), 字段.get("医案"),
                         "03_jingfang.md %s" % 标题, "李可"))

    con = sqlite3.connect(DB)
    try:
        con.execute("DELETE FROM 李可验方 WHERE source='李可'")
        con.executemany(
            "INSERT INTO 李可验方(六经,方名,组成,主治,方解,用法,医案,出处,source)"
            " VALUES(?,?,?,?,?,?,?,?,?)", 数据)
        con.commit()
        print("  入 %d 首" % len(数据))
        print("  按经：%s" % dict(Counter(d[0] for d in 数据)))
        有医案 = sum(1 for d in 数据 if d[6])
        print("  带医案：%d 首 / 带方解：%d 首"
              % (有医案, sum(1 for d in 数据 if d[4])))
        print("\n抽查：")
        for k in ("太阳", "少阳", "少阴", "厥阴"):
            for 名, 组 in con.execute(
                    "SELECT 方名,组成 FROM 李可验方 WHERE 六经=? LIMIT 2", (k,)):
                print("  [%s] %-14s %s" % (k, 名, (组 or "")[:44]))
    finally:
        con.close()


if __name__ == "__main__":
    main()
