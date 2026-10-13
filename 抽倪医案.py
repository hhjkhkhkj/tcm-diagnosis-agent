# -*- coding: utf-8 -*-
"""⑥ 医案 · 倪海厦临床病历（951 份）→ T13 医案 + T14 处方 + T15 针灸处方

源：ni_depth/clinical_records/clinical_records_2008*.md（4 个文件，繁体）
每个案例形如：
    ## 案例 1: 來診原因：xxxxx
    ```
    初診日期：2008/3/21
    003-1 B,YR 女 72 歲 身材中等 2008/3/21
    來診原因：…
    問診：
    1、睡眠：…
    脈診：…
    望診：
    1、舌診：…
    2、眼診：…
    耳診：…
    診斷：…
    針灸處方：
    中藥處方：
    1、 旋覆花三錢 代赭石三錢 厚朴二錢
    …
    五付 九碗水煮三碗 早晚飯前
    解說：
    備註：
    ---PAGE---
    ```

中药处方解析：
  - 「序号」= 「1、」「2、」；
  - 药+剂量成对，中文数字+单位（錢/兩/枚/升/斤/片/分/克）；
  - 「各」字要拆（本方 2008 年那批多为逐味写法，仍兼容「各X錢」）；
  - 抓完药对后，行末残留（如「豬苓湯」「補腎補鈣補骨質」）当注释丢弃。

幂等：按 source='倪海厦' 先删后插（连带处方/针灸）。
"""
import os
import re
import sqlite3
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
基 = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references\ni_depth\clinical_records"
SOURCE = "倪海厦"
书 = "倪海厦临床医案2008"

标签 = ["問診：", "脈診：", "望診：", "耳診：", "診斷：", "針灸處方：",
        "中藥處方：", "解說：", "備註：", "---PAGE---"]

# 药名 + 剂量：剂量 = 中文数字 + 单位
药对 = re.compile(r"([\u4e00-\u9fff]{1,7}?)([一二三四五六七八九十百千]+"
                  r"(?:錢|兩|两|升|斤|枚|片|分|克|個|个))")
单位 = re.compile(r"[錢兩两升斤枚片分克個个]$")
称呼 = re.compile(r"(女|男|Female|Male|F|M)\b")
年龄 = re.compile(r"(\d+\s*歲|\d+\s*岁|約\s*\d+\s*歲|\d+)")


def 取段(全, 标, 止=None):
    i = 全.find(标)
    if i < 0:
        return None
    i += len(标)
    j = len(全)
    for e in (止 or 标签):
        k = 全.find(e, i)
        if 0 <= k < j:
            j = k
    v = 全[i:j].strip()
    return v or None


def 解析药对(行):
    """→ [(药, 剂量, 单位)]，并处理「各X錢」"""
    out = []
    pos = 0
    for m in 药对.finditer(行):
        药, 量 = m.group(1), m.group(2)
        # 「各」出现在药名末尾（如「桂枝、芍药各三錢」）→ 药名去掉「各」
        药 = 药.rstrip("各")
        if not 药:
            continue
        out.append((药, 量, 单位.search(量).group(0) if 单位.search(量) else None))
        pos = m.end()
    return out


def 解析处方块(块):
    """中藥處方块 → [(序号, 药, 剂量, 单位, 特殊煎法)]"""
    if not 块:
        return []
    rows = []
    cur = 0
    煎 = None
    for 线 in 块.splitlines():
        线 = 线.strip()
        if not 线:
            continue
        m = re.match(r"^(\d+)\s*[、.．]\s*(.*)$", 线)
        if m:
            cur = int(m.group(1))
            线 = m.group(2)
        # 煎服法行：无药对，含「付/碗/服/煮」
        if not 药对.search(线):
            if re.search(r"(付|碗|煮|服|先煎|後下|另包)", 线):
                煎 = (煎 + " " + 线) if 煎 else 线
            continue
        for 药, 量, u in 解析药对(线):
            rows.append((cur, 药, 量, u, None))
    if 煎:
        rows = [(a, b, c, d, 煎) for a, b, c, d, _ in rows]
    return rows


def 解析针灸(块):
    if not 块:
        return []
    穴 = []
    for 线 in 块.splitlines():
        for t in re.split(r"[，,、\s]+", 线.strip()):
            t = t.strip("。.．:：")
            if t and re.search(r"[\u4e00-\u9fff]", t) and len(t) <= 12:
                穴.append(t)
    return [(t, None, None) for t in 穴]


def 抽文件(路径):
    名 = os.path.basename(路径)
    文本 = open(路径, encoding="utf-8").read()
    块 = re.split(r"^##\s*案例\s*(\d+)\s*[:：]\s*(.*)$", 文本, flags=re.M)
    案例 = []
    for i in range(1, len(块) - 2, 3):
        号, 标题, 正文 = 块[i], 块[i + 1].strip(), 块[i + 2]
        mb = re.search(r"```(.*?)```", 正文, re.S)
        全 = (mb.group(1) if mb else 正文).strip()
        if not 全:
            continue

        # 主诉：标题里「來診原因：」之后；标题为空则回落正文
        m = re.search(r"來診原因[:：]\s*(.*)$", 标题)
        主诉 = (m.group(1) if m else 标题).strip() or None
        if not 主诉:
            m = re.search(r"來診原因[:：]\s*([^\n]{2,200})", 全)
            if m:
                主诉 = m.group(1).strip()

        日期 = None
        m = re.search(r"初診日期[:：]\s*([0-9/\-]+)", 全)
        if m:
            日期 = m.group(1)
        性别 = 年龄v = 体型 = None
        for 线 in 全.splitlines()[:6]:
            if 称呼.search(线) and re.search(r"\d", 线):
                性别 = 称呼.search(线).group(1)
                m = re.search(r"(\d+)\s*[歲岁]", 线)
                if m:
                    年龄v = m.group(1)
                for k in ("中等", "瘦", "胖", "矮", "高", "壯", "壮"):
                    if k in 线:
                        体型 = k
                        break
                break

        rec = {
            "源id": "%s#%s" % (名, 号), "日期": 日期, "性别": 性别,
            "年龄": 年龄v, "体型": 体型, "主诉": 主诉,
            "西医病名": None, "问诊": 取段(全, "問診："), "望诊": 取段(全, "望診："),
            "眼诊": None, "耳诊": 取段(全, "耳診："), "诊断": 取段(全, "診斷："),
            "六经": None, "体质": None,
            "针灸处方": 取段(全, "針灸處方：", ["中藥處方："] + 标签),
            "治法": None, "转归": 取段(全, "解說：", ["備註："] + 标签),
            "备注": 取段(全, "備註：", 标签),
            "全文": 全, "抽取方式": "确定性正则",
        }
        望 = rec["望诊"]
        if 望:
            m = re.search(r"舌診[:：]\s*(.*?)(?=\d+、|$)", 望, re.S)
            if m:
                rec["望诊"] = m.group(1).strip()
            m = re.search(r"眼診[:：]\s*(.*?)(?=\d+、|$)", 望, re.S)
            if m:
                rec["眼诊"] = m.group(1).strip()
        m = re.search(r"脈診[:：]\s*(.*?)(?=\n\S*[:：]|$)", 全, re.S)
        if m:
            rec["问诊"] = (rec["问诊"] or "") + "\n【脈診】" + m.group(1).strip()

        rec["_处方"] = 解析处方块(取段(全, "中藥處方：", ["解說：", "備註："] + 标签))
        rec["_针灸"] = 解析针灸(取段(全, "針灸處方：", ["中藥處方："] + 标签))
        案例.append(rec)
    return 案例


def main():
    con = sqlite3.connect(DB)
    try:
        旧id = [r[0] for r in con.execute("SELECT id FROM 医案 WHERE source=?", (SOURCE,))]
        if 旧id:
            q = ",".join("?" * len(旧id))
            con.execute("DELETE FROM 处方 WHERE 医案id IN (%s)" % q, 旧id)
            con.execute("DELETE FROM 针灸处方 WHERE 医案id IN (%s)" % q, 旧id)
            con.execute("DELETE FROM 医案 WHERE source=?", (SOURCE,))

        n案 = n方 = n针 = 0
        文件 = sorted(glob.glob(os.path.join(基, "clinical_records_*.md")))
        for p in 文件:
            for r in 抽文件(p):
                cur = con.execute(
                    "INSERT INTO 医案(书,source,源id,日期,性别,年龄,体型,主诉,西医病名,"
                    "问诊,望诊,眼诊,耳诊,诊断,六经,体质,针灸处方,治法,转归,备注,全文,抽取方式)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (书, SOURCE, r["源id"], r["日期"], r["性别"], r["年龄"], r["体型"],
                     r["主诉"], r["西医病名"], r["问诊"], r["望诊"], r["眼诊"], r["耳诊"],
                     r["诊断"], r["六经"], r["体质"], r["针灸处方"], r["治法"], r["转归"],
                     r["备注"], r["全文"], r["抽取方式"]))
                案id = cur.lastrowid
                n案 += 1
                for 序, 药, 量, u, 煎 in r["_处方"]:
                    con.execute("INSERT INTO 处方(医案id,药,剂量,书,source,剂量单位,"
                                "特殊煎法,序号) VALUES(?,?,?,?,?,?,?,?)",
                                (案id, 药, 量, 书, SOURCE, u, 煎, 序))
                    n方 += 1
                for 穴, 手, 依 in r["_针灸"]:
                    con.execute("INSERT INTO 针灸处方(医案id,穴,手法,依据,出处)"
                                " VALUES(?,?,?,?,?)", (案id, 穴, 手, 依, r["源id"]))
                    n针 += 1
        con.commit()
        print("  倪医案 %d 份 ｜ 处方行 %d ｜ 针灸穴 %d" % (n案, n方, n针))
        print("\n字段填充率：")
        for 列 in ("日期", "性别", "年龄", "主诉", "问诊", "望诊", "眼诊", "耳诊", "诊断", "针灸处方"):
            n = con.execute("SELECT COUNT(*) FROM 医案 WHERE source=? AND \"%s\" IS NOT NULL"
                            " AND \"%s\"!=''" % (列, 列), (SOURCE,)).fetchone()[0]
            print("  %-6s %d/%d" % (列, n, n案))
        print("\n抽查·案例1 处方前 6 味：")
        r = con.execute("SELECT id FROM 医案 WHERE source=? AND 源id LIKE '%#1' LIMIT 1",
                        (SOURCE,)).fetchone()
        if r:
            for x in con.execute("SELECT 序号,药,剂量,剂量单位 FROM 处方 WHERE 医案id=?"
                                 " ORDER BY id LIMIT 6", (r[0],)):
                print("   ", x)
        print("\n处方剂量频次 top8：")
        for x in con.execute("SELECT 剂量,COUNT(*) FROM 处方 WHERE source=? GROUP BY 剂量"
                             " ORDER BY COUNT(*) DESC LIMIT 8", (SOURCE,)):
            print("   %-8s %d" % x)
    finally:
        con.close()


if __name__ == "__main__":
    main()
