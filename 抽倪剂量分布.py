# -*- coding: utf-8 -*-
"""⑥附 · 倪海厦处方剂量分布 → 剂量分布（source='倪海厦'，单位='錢'）

⚠️ 不折算成克。理由：本语料里有**两套**折算体系——
   李可/柯雪帆考据：汉制 1 两 = 15.6g（1 钱 = 1.56g）
   倪海厦沿用台制  ：1 钱 = 3.75g（1 两 = 10 钱）
   两者差 2.4 倍，选错就把整张剂量表污染了。所以原样以「錢」统计，另加 单位 列标明。

枚/片/升/個 等非重量单位不进统计（共 309 行）。
幂等：按 source='倪海厦' 先删后插。
"""
import os
import re
import sqlite3
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")

汉 = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
      "六": 6, "七": 7, "八": 8, "九": 9}
量词 = re.compile(r"^([一二三四五六七八九十半]+)(錢|兩|两)$")


def 转錢(量):
    """'三錢'→3.0  '一兩'→10.0  '半錢'→0.5  '十錢'→10.0"""
    m = 量词.match(量)
    if not m:
        return None
    n, u = m.group(1), m.group(2)
    if n == "半":
        v = 0.5
    elif "十" in n:
        a, _, b = n.partition("十")
        v = (汉.get(a, 1) if a else 1) * 10 + (汉.get(b, 0) if b else 0)
    else:
        v = float(sum(汉.get(c, 0) for c in n))
    return v * (10 if u in ("兩", "两") else 1)


def main():
    con = sqlite3.connect(DB)
    try:
        try:
            con.execute("ALTER TABLE 剂量分布 ADD COLUMN 单位 TEXT")
        except sqlite3.OperationalError:
            pass
        con.execute("UPDATE 剂量分布 SET 单位='克' WHERE source='问止' AND (单位 IS NULL OR 单位='')")

        数 = {}
        for 药, 量 in con.execute("SELECT 药,剂量 FROM 处方 WHERE source='倪海厦'"):
            v = 转錢(量 or "")
            if v is None:
                continue
            数.setdefault(药, []).append(v)

        out = []
        for 药, vs in 数.items():
            vs.sort()
            out.append((药, len(vs), min(vs), statistics.median(vs),
                        max(vs), round(statistics.mean(vs), 2), "錢"))
        con.execute("DELETE FROM 剂量_观测_倪")
        con.executemany("INSERT INTO 剂量_观测_倪(药,出现次数,最小,中位,最大,均值,单位)"
                        " VALUES(?,?,?,?,?,?,?)", out)
        con.commit()

        总行 = con.execute("SELECT COUNT(*) FROM 处方 WHERE source='倪海厦'").fetchone()[0]
        print("  纳入统计 %d 味药（来自 %d 行处方，%d 行因非重量单位未计入）"
              % (len(out), 总行, 总行 - sum(len(v) for v in 数.values())))
        print("\n=== 倪海厦 常用药剂量（单位：錢）top12（按出现次数）===")
        for r in con.execute("SELECT 药,出现次数,最小,中位,最大,均值 FROM 剂量_观测_倪"
                             " ORDER BY 出现次数 DESC LIMIT 12"):
            print("  %-5s 出现%-4d  最小%-5s 中位%-5s 最大%-5s 均值%s" % r)
        print("\n=== 与问止对照（走 剂量_观测 视图，两套体系并列）===")
        for 药 in ("桂枝", "白芍", "炙甘草", "茯苓", "白朮"):
            r = con.execute("SELECT source,出现次数,中位,单位 FROM 剂量_观测 WHERE 药=?"
                            " ORDER BY source", (药,)).fetchall()
            if r:
                print("  %-4s %s" % (药, "  ｜  ".join(
                    "%s: %s%s(中位, n=%d)" % (s, m, u, n) for s, n, m, u in r)))
            else:
                print("  %-4s （无）" % 药)
    finally:
        con.close()


if __name__ == "__main__":
    main()
