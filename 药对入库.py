# -*- coding: utf-8 -*-
"""
把重建出来的表格入库

书里有两张表（结构一样，行钥匙不同）：
  药对表   药对 → 主治 → 应用
  单味药表 单味药 → 主治 → 应用
分别入 药对 / 单味药 两张表。

去重：同一行钥匙出现多次时取「应用」的**众数**版本
      ——串行污染只会往末尾加东西，众数自然干净。
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from 归一规则 import 药对名, 归一药          # noqa: E402

SRC = HERE
DB = os.path.join(HERE, "中医大脑医案库.db")
SRCJSON = os.path.join(HERE, "_药对表.json")


def 收(tables, kind):
    """kind: '药对' — 需要 + 分隔；'单味药' — 单个药名"""
    rows = []
    for t in tables:
        for r in t["rows"]:
            key = (r["key"] or "").strip()
            if len(key) < 2:
                continue
            if kind == "药对":
                名 = 药对名(key)
                if not 名:
                    continue
            else:
                名 = 归一药(re.sub(r"\s+", "", key))
                if not re.fullmatch(r"[一-鿿]{2,6}", 名 or ""):
                    continue
            应 = (r["cells"].get("应用") or "").strip()
            if not 应:
                continue
            rows.append({"名": 名,
                         "主治": (r["cells"].get("主治") or "").strip(),
                         "应用": 应,
                         "页": "%s p%d" % (t["pdf"][-6:], t["page"])})
    return rows


def 去重(rows):
    """同一钥匙取「应用」众数；并列取最短"""
    组 = defaultdict(list)
    for r in rows:
        组[r["名"]].append(r)
    out = []
    for k, g in 组.items():
        c = Counter(x["应用"] for x in g)
        top = max(c.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
        r = next(x for x in g if x["应用"] == top)
        out.append(dict(r, 次数=len(g)))
    return sorted(out, key=lambda x: -x["次数"])


def main():
    data = json.load(open(SRCJSON, encoding="utf-8"))
    药对表 = [t for t in data if t.get("key_col") == "药对"]
    单味表 = [t for t in data if t.get("key_col") == "单味药"]

    p = 去重(收(药对表, "药对"))
    d = 去重(收(单味表, "单味药"))

    con = sqlite3.connect(DB)
    c = con.cursor()
    for tbl, rows in [("药对", p), ("单味药", d)]:
        c.execute("DROP TABLE IF EXISTS %s" % tbl)
        c.execute("CREATE TABLE %s(id INTEGER PRIMARY KEY, 名 TEXT, 主治 TEXT, 应用 TEXT, 出处 TEXT, 次数 INTEGER)" % tbl)
        for r in rows:
            c.execute("INSERT INTO %s(名,主治,应用,出处,次数) VALUES(?,?,?,?,?)" % tbl,
                      (r["名"], r["主治"], r["应用"], r["页"], r["次数"]))
    con.commit()
    con.close()

    with open(os.path.join(SRC, "_药对表.txt"), "w", encoding="utf-8") as f:
        f.write("## 药对（%d 条）\n药对\t主治\t应用\t出处\t次数\n" % len(p))
        for r in p:
            f.write("%s\t%s\t%s\t%s\t%d\n" % (r["名"], r["主治"], r["应用"], r["页"], r["次数"]))
        f.write("\n## 单味药（%d 条）\n单味药\t主治\t应用\t出处\t次数\n" % len(d))
        for r in d:
            f.write("%s\t%s\t%s\t%s\t%d\n" % (r["名"], r["主治"], r["应用"], r["页"], r["次数"]))

    print("药对表页   : %d   -> %d 条" % (len(药对表), len(p)))
    print("单味药表页 : %d   -> %d 条\n" % (len(单味表), len(d)))

    print("=== 药对 Top15 ===")
    for r in p[:15]:
        print("   %-22s %2d次  %s" % (r["名"], r["次数"], r["应用"][:40]))
    print("\n=== 单味药 Top15 ===")
    for r in d[:15]:
        print("   %-10s %2d次  %s" % (r["名"], r["次数"], r["应用"][:44]))


if __name__ == "__main__":
    main()
