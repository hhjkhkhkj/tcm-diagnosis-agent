# -*- coding: utf-8 -*-
"""
库扩展 —— 给 中医大脑医案库.db 补表

分两批：

【第一批 · 守拙自己的】
  我的病案   ：守拙自己的接诊记录（四诊 + 证型 + 方 + 剂量）—— 四层路线的第④层
  问诊记录   ：每次 AI 问诊的输入输出，留痕可回溯
  评测记录   ：评测跑分结果

【第二批 · 主干层 T1-T16（倪海厦 + 李可）】2026-10-13
  依据：D:\\Download\\中医大脑_表结构设计_v1.md
  分层：倪李主干 / 问止供给 / 通用层最低

  T1  六经        六经速查（6 行）
  T2  六经公式    症状组 → 经（含排除规则，可集合匹配）
  T3  方剂        跨源方剂表（组成不带剂量）
  T4  合病        合病 / 并病
  T5  场景路由    什么情况用谁的思路
  T6  安全规则    双师共同底线（硬性规则）
  T7  双师对照    同一证型两种思路
  T8  剂量决策    轻 / 中 / 重三档走谁的剂量
  T9  关键词路由  关键词 → 对象 id（原 SKILL.md 指向 .md 文件，已改写）
  T10 剂量分布（观测）：现有表 + source 列，另建同义视图 剂量_观测
  T10b 剂量_观测_倪：倪案处方实测剂量分布，单位「錢」——与问止的「克」不混算
  T11 剂量_规范   倪李的规定值（经方原量/基础有效量/常用量/最大量）
  T12 本草        倪 / 李 / 问止 三源并列，行带 source + 可信度
  T13 医案（改造）：加列，不重建
  T14 处方（改造）：加列，不重建
  T15 针灸处方    倪独有
  T16 文献块      条文注解 / 讲义 / 古籍（散文走这里，不上向量）
  T17 李可验方    六经验方（带剂量+方解+用法+医案），源 03_jingfang.md §八-§十

  ⚠️ 只加不删。现有 医案 / 处方 / 剂量分布 三张表的数据一行不动。

用法：py 库扩展.py        （建表，幂等）
      py 库扩展.py -s     （只打印全部表结构）
"""
import os
import sqlite3
import sys

DB = r"D:\Download\医案集_OCR\中医大脑医案库.db"

DDL_自己的 = """
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

DDL_主干 = """
-- ===== T1 六经 ===== 源 SKILL.md#六经速查表
CREATE TABLE IF NOT EXISTS 六经(
  id INTEGER PRIMARY KEY,
  经 TEXT UNIQUE,       -- 太阳/阳明/少阳/太阴/少阴/厥阴
  病位 TEXT,            -- 表 / 里（热）/ 半表半里 / 里（寒）/ 里（虚）/ 里（杂）
  主证 TEXT,
  主方 TEXT,
  要点 TEXT,
  source TEXT DEFAULT 'ni_li',
  出处 TEXT
);

-- ===== T2 六经公式 ===== 源 SKILL.md#六经辨证诊断公式
-- 类型=鉴别 的行是「排除规则」，价值不低于命中的那些
CREATE TABLE IF NOT EXISTS 六经公式(
  id INTEGER PRIMARY KEY,
  类型 TEXT,            -- 主证 / 鉴别
  症状组 TEXT,          -- JSON 数组 ["恶寒","脉浮","头项强痛"]
  判定 TEXT,            -- 太阳病
  排除 TEXT,            -- 鉴别行专用：恶寒+脉沉 → 少阴（非太阳）
  出处 TEXT
);

-- ===== T3 方剂 ===== 源 SKILL.md#经方核心方剂速查 + li_depth/03_jingfang
-- 组成不带剂量：同一首桂枝汤，倪是「三钱」、李是「45g」，剂量下沉到 T10/T11
CREATE TABLE IF NOT EXISTS 方剂(
  id INTEGER PRIMARY KEY,
  source TEXT,          -- ni / li / 通用 / 问止
  方名 TEXT,
  别名 TEXT,
  组成 TEXT,            -- 药味串，不带剂量
  主治 TEXT,
  六经 TEXT,
  出处 TEXT
);

-- ===== T4 合病 ===== 源 SKILL.md#合病与并病
CREATE TABLE IF NOT EXISTS 合病(
  id INTEGER PRIMARY KEY,
  类型 TEXT,            -- 合病 / 并病
  含义 TEXT,
  治则 TEXT,
  症状特征 TEXT,
  方剂 TEXT,
  出处 TEXT
);

-- ===== T5 场景路由 ===== 源 SKILL.md#场景路由
CREATE TABLE IF NOT EXISTS 场景路由(
  id INTEGER PRIMARY KEY,
  场景 TEXT,
  用谁 TEXT,            -- 倪 / 李 / 倪针+李药 / 双方案并列
  判据 TEXT,
  提示 TEXT,            -- ⚠️ 如「必须提醒立即送医」
  指向 TEXT,            -- 该查哪些对象（T3方剂 / T10-T11剂量 / T16文献块）
  出处 TEXT
);

-- ===== T6 安全规则 ===== 源 SKILL.md#安全边界
CREATE TABLE IF NOT EXISTS 安全规则(
  id INTEGER PRIMARY KEY,
  规则 TEXT,
  倪原话 TEXT,
  李原话 TEXT,
  硬性 TEXT,            -- 是 / 否（「急症送医」=是）
  出处 TEXT
);

-- ===== T7 双师对照 ===== 源 SKILL.md#双师对照
CREATE TABLE IF NOT EXISTS 双师对照(
  id INTEGER PRIMARY KEY,
  组号 TEXT,            -- 对照一 / 对照二 …
  组名 TEXT,            -- 如「少阴病心衰（四逆汤证）」
  维度 TEXT,            -- 辨证 / 方剂 / 剂量 / 思路 / 原话 / 选择原则
  倪 TEXT,
  李 TEXT,
  同异 TEXT,            -- 同 / 异
  出处 TEXT
);

-- ===== T8 剂量决策 ===== 源 SKILL.md#剂量选择决策树
CREATE TABLE IF NOT EXISTS 剂量决策(
  id INTEGER PRIMARY KEY,
  层级 TEXT,            -- 轻证 / 中证 / 重证
  判据 TEXT,
  走谁 TEXT,
  示例 TEXT,
  须遵守 TEXT,          -- 重证 5 条硬约束（炙甘草≥附子、武火急煎≥1h…）
  出处 TEXT
);

-- ===== T9 关键词路由 ===== 源 SKILL.md#关键词→模块细粒度路由表
-- ⚠️ 原文指向 .md 文件，入库后那些文件不存在 → 改成指向对象 id
CREATE TABLE IF NOT EXISTS 关键词路由(
  id INTEGER PRIMARY KEY,
  关键词 TEXT,
  类别 TEXT,            -- 症状 / 证型 / 药物 / 疾病 / 方剂
  目标表 TEXT,          -- 方剂 / 本草 / 文献块 / 六经 …
  目标id INTEGER,
  原文路径 TEXT,        -- 保留原始 .md 路径，便于回溯核对
  出处 TEXT
);

-- ===== T11 剂量_规范 ===== 源 li_depth/modules/03_jingfang.md
-- 「规定值」：这家认为应该这么用。区别于 剂量分布 的「观测值」
CREATE TABLE IF NOT EXISTS 剂量_规范(
  id INTEGER PRIMARY KEY,
  药 TEXT,
  家 TEXT,              -- ni / li
  经方原量 TEXT,        -- 如 "2~3两"
  基础有效量 TEXT,      -- 如 "30~45"
  常用量 TEXT,
  最大量 TEXT,
  出处 TEXT
);

-- ===== T12 本草 ===== 三源并列
CREATE TABLE IF NOT EXISTS 本草(
  id INTEGER PRIMARY KEY,
  药名 TEXT,
  source TEXT,          -- ni / li / wenzhi
  品级 TEXT,            -- 上品 / 中品 / 下品
  性味 TEXT,
  主治 TEXT,
  应用 TEXT,
  用量 TEXT,
  原文 TEXT,
  出处 TEXT,
  可信度 TEXT           -- 原文 / 二手蒸馏（倪那批注释是 TCM-Skill 自己整理的二手）
);

-- ===== T15 针灸处方 ===== 倪独有（融合表：针灸→倪独）
CREATE TABLE IF NOT EXISTS 针灸处方(
  id INTEGER PRIMARY KEY,
  医案id INTEGER,
  穴 TEXT,
  手法 TEXT,
  依据 TEXT,
  出处 TEXT
);

-- ===== T10b 剂量_观测_倪 ===== 倪海厦处方实测剂量分布
-- 为什么不并进 剂量分布：那张表的 药 是 PRIMARY KEY（单源设计），多源要重建表，
-- 而设计文档定了「剂量分布不重建」。故另立同构表，用 剂量_观测 视图 UNION 出去。
-- 单位是「錢」不是克——本语料里倪用台制(1钱=3.75g)、李可用汉制(1两=15.6g)，差 2.4 倍，不混算。
CREATE TABLE IF NOT EXISTS 剂量_观测_倪(
  药 TEXT PRIMARY KEY,
  出现次数 INTEGER,
  最小 REAL, 中位 REAL, 最大 REAL, 均值 REAL,
  单位 TEXT
);

-- ===== T16 文献块 ===== 散文走这里（条文注解 / 讲义 / 古籍 / 日志）
-- 不上向量：条文和讲义在向量空间全是近邻，理由同 README 第一节
CREATE TABLE IF NOT EXISTS 文献块(
  id INTEGER PRIMARY KEY,
  source TEXT,          -- ni / li / 古籍
  书 TEXT,              -- 伤寒论 / 金匮要略 / 黄帝内经 / 梁冬对话 …
  篇 TEXT,
  条号 TEXT,
  标题 TEXT,
  正文 TEXT,
  字数 INTEGER,
  关键词 TEXT,          -- JSON 数组
  出处 TEXT
);

-- ===== T17 李可验方 ===== 源 li_depth/03_jingfang.md §八-§十（六经验方）
-- 与 T3 方剂分开：这批的剂量是方内定值，且自带 方解/用法/医案，T3（组成不带剂量）装不下
CREATE TABLE IF NOT EXISTS 李可验方(
  id INTEGER PRIMARY KEY,
  六经 TEXT,            -- 太阳/阳明/少阳/太阴/少阴/厥阴
  方名 TEXT,
  组成 TEXT,            -- 带剂量原文
  主治 TEXT,
  方解 TEXT,
  用法 TEXT,
  医案 TEXT,
  出处 TEXT,
  source TEXT DEFAULT '李可'
);
"""

# 现有表 → 要补的列（只加不删）
改造列 = {
    "医案": [
        ("source", "TEXT"), ("源id", "TEXT"), ("日期", "TEXT"), ("性别", "TEXT"),
        ("年龄", "TEXT"), ("体型", "TEXT"), ("主诉", "TEXT"), ("西医病名", "TEXT"),
        ("问诊", "TEXT"), ("望诊", "TEXT"), ("眼诊", "TEXT"), ("耳诊", "TEXT"),
        ("诊断", "TEXT"), ("六经", "TEXT"), ("体质", "TEXT"), ("针灸处方", "TEXT"),
        ("治法", "TEXT"), ("转归", "TEXT"), ("备注", "TEXT"), ("全文", "TEXT"),
        ("抽取方式", "TEXT"),   # 人 / 脚本 / 模型
    ],
    "处方": [
        ("source", "TEXT"), ("剂量单位", "TEXT"), ("特殊煎法", "TEXT"), ("序号", "INTEGER"),
    ],
    "剂量分布": [
        ("source", "TEXT"), ("单位", "TEXT"),   # 问止=克 / 倪海厦=錢（两套折算体系，不混算）
    ],
}

书字段表 = ["医案", "处方", "药对", "单味药", "体质", "加减", "方性", "结构符合方"]
第一本 = "医案集1"

新建表 = ["六经", "六经公式", "方剂", "合病", "场景路由", "安全规则", "双师对照",
          "剂量决策", "关键词路由", "剂量_规范", "剂量_观测_倪", "本草", "针灸处方", "文献块",
          "李可验方"]


def _列(con, t):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % t)]


def 加书字段(con):
    """给各表补「书」字段，多本书共存；老数据标成第一本"""
    for t in 书字段表:
        try:
            if "书" not in _列(con, t):
                con.execute("ALTER TABLE %s ADD COLUMN 书 TEXT" % t)
            con.execute("UPDATE %s SET 书=? WHERE 书 IS NULL OR 书=''" % t, (第一本,))
        except Exception:
            pass


def 加列(con):
    """给现有表补列（幂等），并给老数据回填 source"""
    改了 = []
    for t, cols in 改造列.items():
        try:
            现有 = _列(con, t)
        except Exception:
            continue          # 表不存在，跳过
        if not 现有:
            continue
        for 名, 型 in cols:
            if 名 not in 现有:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (t, 名, 型))
                改了.append("%s.%s" % (t, 名))
        # 回填：老数据都是问止的
        if "source" in _列(con, t):
            con.execute("UPDATE %s SET source='问止' WHERE source IS NULL OR source=''" % t)
    return 改了


def 建视图(con):
    """剂量_观测 = 「看观测剂量的唯一入口」，把两源 UNION 出去
      · 剂量分布   —— 问止，单位：克（建库.py 在写它，不改名）
      · 剂量_观测_倪 —— 倪海厦，单位：錢
    保留 source/单位 两列，调用方自己按 source 分流，别跨源直接比大小。"""
    con.execute("DROP VIEW IF EXISTS 剂量_观测")
    con.execute("""CREATE VIEW 剂量_观测 AS
                     SELECT 药,出现次数,最小,中位,最大,均值,source,单位 FROM 剂量分布
                     UNION ALL
                     SELECT 药,出现次数,最小,中位,最大,均值,'倪海厦',单位 FROM 剂量_观测_倪""")


def 建表(db=DB):
    con = sqlite3.connect(db)
    con.executescript(DDL_自己的)
    con.executescript(DDL_主干)
    加书字段(con)
    改了 = 加列(con)
    建视图(con)
    con.commit()

    n = {}
    for t in (["医案", "处方", "药对", "单味药", "体质", "剂量分布", "词表", "方名表",
              "加减", "方性", "结构符合方", "我的病案", "问诊记录", "评测记录"]
              + 新建表):
        try:
            n[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except Exception:
            n[t] = None
    con.close()
    return n, 改了


def 打印结构(db=DB):
    con = sqlite3.connect(db)
    for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cnt = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
        print("\n%s  (%d 条)" % (t, cnt))
        for c in con.execute('PRAGMA table_info("%s")' % t):
            print("    %-14s %s" % (c[1], c[2]))
    con.close()


if __name__ == "__main__":
    if "-s" in sys.argv:
        打印结构()
        sys.exit()

    n, 改了 = 建表()
    print("表状态：")
    for k, v in n.items():
        s = "（表不存在）" if v is None else ("（空）" if v == 0 else "%d 条" % v)
        print("   %-12s %s" % (k, s))
    if 改了:
        print("\n新加的列：%d 个" % len(改了))
        for x in 改了:
            print("   %s" % x)
    else:
        print("\n没有新列要加（已是最新）")
