# -*- coding: utf-8 -*-
"""④ 本草 · 把通用层《神农本草经》三品注释(356味)落进 本草

来源：skill/references/modules/18|19|20_本草经_{上,中,下}品注释.md
每个药条形如：
    ### 药名
    （空行）
    味甘、微寒。          ← 性味（经文）
    主……。（……）          ← 主治（经文）
    《吴普》曰……            ← 诸家注（算原文的一部分）
    **〔本草备要〕**         ← 以下是后人应用，单列
    ……
    ---

幂等：按 source 先删后插。
"""
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
基 = r"D:\Download\_tcm_src\TCM-Skill-main\skill\references\modules"
文件 = [
    ("18_本草经_上品注释.md", "上品"),
    ("19_本草经_中品注释.md", "中品"),
    ("20_本草经_下品注释.md", "下品"),
]
SOURCE = "本经"
可信度 = "原文"          # 《神农本草经》经文，非二手转述


def 切药条(txt):
    """按 '### 药名' 切块，返回 [(药名, 正文)]"""
    out = []
    当前名, buf = None, []
    for 线 in txt.splitlines():
        m = re.match(r"^###\s+(.+?)\s*$", 线)
        if m:
            if 当前名:
                out.append((当前名, "\n".join(buf)))
            当前名, buf = m.group(1), []
        elif 当前名 is not None:
            buf.append(线)
    if 当前名:
        out.append((当前名, "\n".join(buf)))
    return out


def 取性味(正文):
    # 中品/下品：**性味**：味苦寒。   上品：裸行「味甘、微寒。」
    m = re.search(r"^\*\*性味\*\*[：:]\s*味?\s*([^\n。]{1,60})", 正文, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"^味\s*([^\n。]{1,60})。", 正文, re.M)
    return m.group(1).strip() if m else None


def 取主治(正文):
    m = re.search(r"^\*\*主治\*\*[：:]\s*主?\s*([^\n。]{2,400})", 正文, re.M)
    if m:
        return m.group(1).strip()
    m = re.search(r"^主\s*([^\n]{2,400}?)。", 正文, re.M)
    return m.group(1).strip() if m else None


def 拆分(正文):
    """返回 (经文原文, 后人应用)"""
    正文 = re.sub(r"\n-{3,}\s*$", "", 正文).strip()
    m = re.search(r"\*\*〔[^〕]*〕\*\*", 正文)
    if m:
        return 正文[:m.start()].strip(), 正文[m.end():].strip()
    return 正文, None


def 入问止(con):
    """问止源：直接用现存 单味药 表（教材口径），不重抽"""
    con.execute("DELETE FROM 本草 WHERE source='问止'")
    con.execute("""INSERT INTO 本草(药名,source,品级,性味,主治,应用,用量,原文,出处,可信度)
                   SELECT 名,'问止',NULL,NULL,主治,应用,NULL,NULL,出处,'教材'
                   FROM 单味药 WHERE 名 IS NOT NULL""")
    return con.execute("SELECT COUNT(*) FROM 本草 WHERE source='问止'").fetchone()[0]


def 入李可(con):
    """李可源：只有「规定量」，没有性味主治——用量单独成串，供剂量层交叉核对"""
    con.execute("DELETE FROM 本草 WHERE source='李可'")
    rows = con.execute("SELECT 药,经方原量,基础有效量,常用量,最大量,出处"
                       " FROM 剂量_规范 WHERE 家='李可'").fetchall()
    out = []
    for 药, 原量, 基础, 常用, 最大, 出处 in rows:
        if not 药:
            continue
        串 = "；".join(u"%s%s" % (k, v) for k, v in
                      (("经方原量", 原量), ("基础有效量", 基础),
                       ("常用量", 常用), ("最大量", 最大)) if v)
        out.append((药, "李可", None, None, None, None, 串, None, 出处, "规定值"))
    con.executemany("INSERT INTO 本草(药名,source,品级,性味,主治,应用,用量,原文,出处,可信度)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)", out)
    return len(out)


def main():
    全部 = []
    for 名, 品 in 文件:
        p = os.path.join(基, 名)
        txt = open(p, encoding="utf-8").read()
        for 药名, 正文 in 切药条(txt):
            药名 = re.sub(r"\s+", "", 药名)
            if not 药名 or len(药名) > 8:      # 过滤掉非药名的分节标题
                continue
            经文, 应用 = 拆分(正文)
            if not 经文:
                continue
            全部.append((药名, SOURCE, 品, 取性味(正文), 取主治(经文),
                         应用, None, 经文, "%s §%s" % (名, 药名), 可信度))

    con = sqlite3.connect(DB)
    try:
        con.execute("DELETE FROM 本草 WHERE source=?", (SOURCE,))
        con.executemany(
            "INSERT INTO 本草(药名,source,品级,性味,主治,应用,用量,原文,出处,可信度)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)", 全部)
        n问止 = 入问止(con)
        n李可 = 入李可(con)
        con.commit()

        print("\n=== 本草 三源 ===")
        for s, n, 可信 in con.execute(
                "SELECT source,COUNT(*),MAX(可信度) FROM 本草 GROUP BY source ORDER BY COUNT(*) DESC"):
            print("  %-6s %4d 味   可信度=%s" % (s, n, 可信))
        print("\n  本经：有性味 %d，有主治 %d，有应用 %d" % (
            con.execute("SELECT COUNT(*) FROM 本草 WHERE source='本经' AND 性味 IS NOT NULL").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM 本草 WHERE source='本经' AND 主治 IS NOT NULL").fetchone()[0],
            con.execute("SELECT COUNT(*) FROM 本草 WHERE source='本经' AND 应用 IS NOT NULL").fetchone()[0]))
        print("  分品：", dict(con.execute(
            "SELECT 品级,COUNT(*) FROM 本草 WHERE source='本经' GROUP BY 品级").fetchall()))

        print("\n=== 同一味药三源并列（看「甘草」）===")
        for r in con.execute("SELECT source,品级,性味,substr(主治,1,26),用量"
                             " FROM 本草 WHERE 药名='甘草' ORDER BY source"):
            print("  %-5s | %-4s | %-8s | %-28s | %s"
                  % tuple((x if x else "—") for x in r))
    finally:
        con.close()


if __name__ == "__main__":
    main()
