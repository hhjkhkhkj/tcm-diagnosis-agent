# -*- coding: utf-8 -*-
"""③ 剂量层 · 把李可的经方基础有效量总表(45×5)落进 剂量_规范

来源：skill/references/li_depth/modules/03_jingfang.md  第 93-139 行
性质：规定值（"该用多少"），与 剂量分布（问止统计的"实际用了多少"）分表存放。
幂等：先 DELETE 掉 source 为李可的那批再插。
"""
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
SRC = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references\li_depth\modules\03_jingfang.md"
出处 = "li_depth/modules/03_jingfang.md §2.1 经方基础有效量总表"

# 表头列 → 库列
列映射 = {
    "药物": "药",
    "经方原量（两）": "经方原量",
    "基础有效量（克）": "基础有效量",
    "李可常用量（克）": "常用量",
    "李可最大用量（克）": "最大量",
}


def 清值(s):
    s = s.strip()
    if s in ("--", "-", "—", ""):
        return None
    return s


def 抽表():
    txt = open(SRC, encoding="utf-8").read()
    # 只取 §2.1 到下一个 ### 之间
    m = re.search(r"###\s*2\.1.*?\n(.*?)\n###\s*2\.2", txt, re.S)
    if not m:
        raise SystemExit("找不到 §2.1 表格")
    段 = m.group(1)

    行 = []
    for 线 in 段.splitlines():
        线 = 线.strip()
        if not 线.startswith("|"):
            continue
        if set(线) <= set("|-: "):          # 分隔行
            continue
        格 = [c.strip() for c in 线.strip("|").split("|")]
        行.append(格)

    表头 = 行[0]
    缺 = [c for c in 列映射 if c not in 表头]
    if 缺:
        raise SystemExit("表头对不上，缺：%s" % 缺)
    序号 = {c: 表头.index(c) for c in 列映射}

    out = []
    for r in 行[1:]:
        if len(r) < len(表头):
            continue
        rec = {}
        for 源列, 库列 in 列映射.items():
            rec[库列] = 清值(r[序号[源列]])
        if not rec.get("药"):
            continue
        out.append((rec["药"], "李可", rec["经方原量"], rec["基础有效量"],
                    rec["常用量"], rec["最大量"], 出处))
    return out


def main():
    数据 = 抽表()
    con = sqlite3.connect(DB)
    try:
        con.execute("DELETE FROM 剂量_规范 WHERE 家='李可'")
        con.executemany(
            "INSERT INTO 剂量_规范(药,家,经方原量,基础有效量,常用量,最大量,出处)"
            " VALUES(?,?,?,?,?,?,?)", 数据)
        con.commit()
        总 = con.execute("SELECT COUNT(*) FROM 剂量_规范").fetchone()[0]
        print("  剂量_规范 本次入 %d 味，表内合计 %d 味" % (len(数据), 总))
        print("\n抽查：")
        for 药 in ("制附片", "麻黄", "黄芪", "炙甘草"):
            r = con.execute("SELECT 经方原量,基础有效量,常用量,最大量 FROM 剂量_规范"
                            " WHERE 药=?", (药,)).fetchone()
            print("  %-5s 经方原量=%-8s 基础有效量=%-8s 常用=%-8s 最大=%s"
                  % ((药,) + tuple(x or "-" for x in r)))
    finally:
        con.close()


if __name__ == "__main__":
    main()
