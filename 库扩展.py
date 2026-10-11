# -*- coding: utf-8 -*-
"""
库扩展 —— 给 中医大脑医案库.db 补三张自己的表

  我的病案   ：守拙自己的接诊记录（四诊 + 证型 + 方 + 剂量）—— 这是四层路线的第④层
  问诊记录   ：每次 AI 问诊的输入输出，留痕可回溯
  评测记录   ：评测跑分结果

用法：py 库扩展.py        （建表，幂等）
"""
import os
import sqlite3

DB = r"D:\Download\医案集_OCR\中医大脑医案库.db"

DDL = """
CREATE TABLE IF NOT EXISTS 我的病案(
  id INTEGER PRIMARY KEY,
  日期 TEXT,            -- YYYY-MM-DD
  患者 TEXT,            -- 匿名代号，如 A01
  四诊原文 TEXT,         -- 自由文本
  症状 TEXT,            -- JSON 数组（受控标签）
  舌脉 TEXT,            -- JSON 数组
  证型 TEXT,            -- 他定的证（我不碰）
  方名 TEXT,
  处方 TEXT,            -- JSON: [{药, 量}]
  效果 TEXT,            -- 随访结果，回访后补
  随访日期 TEXT,
  备注 TEXT
);

CREATE TABLE IF NOT EXISTS 问诊记录(
  id INTEGER PRIMARY KEY,
  时间 TEXT,
  输入 TEXT,
  命中标签 TEXT,
  输出 TEXT,
  耗时秒 REAL
);

CREATE TABLE IF NOT EXISTS 评测记录(
  id INTEGER PRIMARY KEY,
  时间 TEXT,
  名称 TEXT,
  样本数 INTEGER,
  指标 TEXT             -- JSON
);
"""


书字段表 = ["医案", "处方", "药对", "单味药", "体质", "加减", "方性", "结构符合方"]
第一本 = "医案集1"


def 加书字段(con):
    """给各表补「书」字段，多本书共存；老数据标成第一本"""
    for t in 书字段表:
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]
            if cols and "书" not in cols:
                con.execute("ALTER TABLE %s ADD COLUMN 书 TEXT" % t)
            con.execute("UPDATE %s SET 书=? WHERE 书 IS NULL OR 书=''" % t, (第一本,))
        except Exception:
            pass


def 建表(db=DB):
    con = sqlite3.connect(db)
    con.executescript(DDL)
    加书字段(con)
    con.commit()
    n = {}
    for t in ["医案", "处方", "药对", "单味药", "体质", "剂量分布", "词表", "方名表",
              "加减", "方性", "结构符合方", "我的病案", "问诊记录", "评测记录"]:
        try:
            n[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except Exception:
            n[t] = None
    con.close()
    return n


if __name__ == "__main__":
    n = 建表()
    print("表状态：")
    for k, v in n.items():
        s = "（表不存在）" if v is None else ("（空）" if v == 0 else "%d 条" % v)
        print("   %-12s %s" % (k, s))
