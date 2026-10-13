# -*- coding: utf-8 -*-
"""中医大脑 · 方剂四选一评测

题目从哪来（不编题，全从库里的事实来）：
  取一条**已有「症状/舌脉」标签、且有真实处方**的医案；
  把它真实吃到的药味，与 T3 `方剂` 里每首方的组成做重叠，
  重叠最好的那首 = 正确选项，另外随机抽 3 首当干扰项。
  → 真值是病人**实际吃到的药**，不是我们估的。

为什么这算公平：
  模型只拿到「症状 + 舌脉标签」和四个**方名**，看不见处方、也看不见方剂组成，
  要答对只能靠「证 → 方」的医学判断。随机基线 = 25%。

繁简：倪案处方是繁体（乾薑/吳茱萸/阿膠），方剂是简体 —— 一律 opencc 转简后再比。

出题质量闸门（保证「有唯一正确答案」）：
  · 正确方必须覆盖 ≥2 味、覆盖率 ≥0.5
  · 正确方须比**最好的干扰项**高出 ≥0.2，否则这题弃掉
  弃题不算进分母，只报「出题成功率」。

用法：
    py 评测.py           跑 30 题
    py 评测.py 50        跑 50 题
落库：`评测记录`(时间/名称/样本数/指标)
"""
import json
import os
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
from opencc import OpenCC

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

from 模型判断 import DS_KEY, DS_URL, DS_MODEL   # noqa: E402

_简 = OpenCC("t2s")
_选项字母 = "ABCD"


def 化简(s):
    return _简.convert(s or "")


# ---------- 方剂侧 ----------

def 拆组成(文):
    """把「麻黄四两、杏仁五十个、炙甘草二两」拆成 [麻黄, 杏仁, 炙甘草]

    方剂表里有的行带着剂量原文（李可那批），这里剥掉数字与量词尾巴。
    """
    出 = []
    for t in re.split(r"[、，,＋+/]", 文):
        t = re.sub(r"^[-—·\s]+", "", t)
        t = re.sub(r"[一二三四五六七八九十百千萬两半].*$", "", t)   # 麻黄四两 → 麻黄
        t = re.sub(r"(以上|如.*|各.*|共.*|加至.*)$", "", t)          # 石膏如鸡子大 → 石膏
        t = t.strip()
        if len(t) >= 2 and t not in 出:
            出.append(t)
    return 出


def 载方剂(con):
    """T3 方剂 → 按方名去重（通用/李可重复的那些只留一首）"""
    见 = {}
    for r in con.execute("SELECT 方名,组成 FROM 方剂 ORDER BY id"):
        名 = (r["方名"] or "").strip()
        if not 名 or "变通要点" in 名 or 名 in 见:
            continue
        文 = 化简(r["组成"] or "")
        药 = 拆组成(文)
        if len(药) < 2:
            continue
        见[名] = {"方名": 名, "文": 文, "药": 药}
    return list(见.values())


# ---------- 病案侧 ----------

def 标签来源名():
    """这一跑用的是哪套标签 —— 落库的「名称」带上它，两次跑才分得开"""
    return "原始标签" if os.environ.get("EVAL_RAW_TAGS", "") else "归一标签"


def 载病案(con, 只要源=None, 用归一=None):
    """有标签、且有真实处方的医案

    用归一：标签从哪来 ——
        True  = `医案因子`（**归一后**的规范因子，检索现在也用这个）
        False = `医案.症状/舌脉`（原始列，带 OCR 变体）
        不传  = 看环境变量 `EVAL_RAW_TAGS`；设了就退回原始列，不设就用归一后的。
    这么留着是为了能跑 A/B：**同一次评测，只换标签来源，看归一到底有没有用**。
    """
    if 用归一 is None:
        用归一 = not os.environ.get("EVAL_RAW_TAGS", "")

    if 用归一:
        sql = ("""SELECT m.id,m.篇,m.source FROM 医案 m
                  WHERE EXISTS(SELECT 1 FROM 医案因子 f WHERE f.医案id=m.id
                                 AND f.维度 IN ('症状','舌脉'))
                    AND EXISTS(SELECT 1 FROM 处方 c WHERE c.医案id=m.id)""")
    else:
        sql = ("""SELECT m.id,m.篇,m.source FROM 医案 m
                  WHERE m.症状 IS NOT NULL AND m.症状 NOT IN ('','[]')
                    AND EXISTS(SELECT 1 FROM 处方 c WHERE c.医案id=m.id)""")
    参 = ()
    if 只要源:
        sql += " AND m.source=?"
        参 = (只要源,)
    出 = []
    for r in con.execute(sql, 参):
        药 = []
        for x in con.execute("SELECT 药 FROM 处方 WHERE 医案id=?", (r["id"],)):
            y = 化简(x["药"]).strip()
            if y and y not in 药:
                药.append(y)
        if len(药) < 2:
            continue
        if 用归一:
            # 归一后的因子：一条一行，按维度归位（**顺带修掉症状/舌脉串列**）
            标 = {"症状": [], "舌脉": []}
            for 维, 值 in con.execute(
                    "SELECT 维度,因子 FROM 医案因子 WHERE 医案id=? AND 维度 IN ('症状','舌脉')",
                    (r["id"],)):
                标[维].append(值)
            标签 = 标["症状"] + 标["舌脉"]
        else:
            try:
                标签 = json.loads(r["症状"] or "[]") + json.loads(r["舌脉"] or "[]")
            except Exception:
                continue
        if not 标签:
            continue
        出.append({"id": r["id"], "篇": r["篇"], "source": r["source"],
                   "标签": 标签, "药": 药})
    return 出


def 打分(方, 案药):
    """覆盖率 = 这首方的组成里，有几味真出现在这案子的处方里"""
    覆 = [t for t in 方["药"] if any(t in y or y in t for y in 案药)]
    return len(覆) / len(方["药"]), 覆


# ---------- 出题 ----------

def 出题(案, 方剂, 干扰数=3, 余量=0.2):
    """返回一道题；不满足唯一正确答案就返回 None"""
    排 = []
    for f in 方剂:
        s, 覆 = 打分(f, 案["药"])
        排.append((s, len(覆), f))
    排.sort(key=lambda x: (-x[0], -x[1]))
    最优分, 最优覆数, 优 = 排[0]

    # 闸门一：正确方得真的对得上
    if 最优覆数 < 2 or 最优分 < 0.5:
        return None

    # 闸门二：干扰项不能也沾边（否则题目没有唯一答案）
    其他 = [x for x in 排[1:] if x[0] > 最优分 - 余量]
    if 其他:
        return None

    # 干扰项只从「跟这案处方几乎不沾边」的方里抽 ——
    # 否则会出现「四逆汤」和「四逆汤原方」同时当选项这种没法答的题
    池 = [x for x in 排[1:] if x[0] < 0.4]
    if len(池) < 干扰数:
        return None
    干扰 = random.sample(池, 干扰数)
    选项 = [优["方名"]] + [f["方名"] for _, _, f in 干扰]
    random.shuffle(选项)
    正确 = _选项字母[选项.index(优["方名"])]

    return {
        "案id": 案["id"], "源": 案["source"],
        "标签": 案["标签"][:24],
        "选项": 选项, "正确": 正确,
        "真方": 优["方名"], "覆盖": 最优覆数, "覆盖率": round(最优分, 2),
        "处方": 案["药"][:16],
    }


def 造提示(题):
    行 = ["%s %s" % (_选项字母[i], n) for i, n in enumerate(题["选项"])]
    return ("下面是一位中医师接诊时记下的症状与舌脉（受控标签）。\n"
            "请判断他当时最可能开的是哪一首方。\n"
            '只输出 JSON：{"选项":"B"}，不要任何别的文字。\n\n'
            "【症状与舌脉】\n" + "、".join(题["标签"]) + "\n\n"
            "【可选方剂】\n" + "\n".join(行))


def 答题(题):
    """子线程：返回 选项字母（'?' = 失败）"""
    for 试 in range(3):
        try:
            r = requests.post(
                DS_URL,
                headers={"Authorization": "Bearer " + DS_KEY,
                         "Content-Type": "application/json"},
                json={"model": DS_MODEL,
                      "messages": [{"role": "user", "content": 造提示(题)}],
                      "reasoning_effort": "low",     # 四选一，不用 high，省一半时间
                      "response_format": {"type": "json_object"}},
                timeout=120)
            r.raise_for_status()
            出 = json.loads(r.json()["choices"][0]["message"]["content"]).get("选项", "")
        except Exception:
            出 = ""
        出 = str(出).strip().upper()[:1]
        if 出 in _选项字母[:len(题["选项"])]:
            return 出
        time.sleep(1 + 试)
    return "?"


# ---------- 主流程 ----------

def 跑(条数=30, 并发=8):
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        方剂 = 载方剂(con)
        病案 = 载病案(con)
        if len(方剂) < 4:
            return {"错误": "方剂不足 4 首，出不了四选一"}

        random.shuffle(病案)
        题 = []
        试过 = 0
        for 案 in 病案:
            试过 += 1
            t = 出题(案, 方剂)
            if t:
                题.append(t)
            if len(题) >= 条数:
                break
        出题率 = round(len(题) / 试过, 3) if 试过 else 0
        if not 题:
            return {"错误": "没有一条病案能出成题（处方与方剂组成对不上）"}

        t0 = time.time()
        对 = 0
        明细 = []
        with ThreadPoolExecutor(max_workers=并发) as 池:
            任务 = {池.submit(答题, t): t for t in 题}
            for fut in as_completed(任务):
                t = 任务[fut]
                选 = fut.result()
                ok = (选 == t["正确"])
                对 += ok
                明细.append({"案id": t["案id"], "源": t["源"], "真方": t["真方"],
                             "正确选项": t["正确"], "模型选": 选, "对": bool(ok),
                             "覆盖": t["覆盖"], "覆盖率": t["覆盖率"]})
        明细.sort(key=lambda x: (x["对"], x["案id"]))
        耗时 = time.time() - t0
        n = len(题)
        准确 = round(对 / n, 3)
        源计 = {}
        for d in 明细:
            源计[d["源"]] = 源计.get(d["源"], 0) + 1
        指标 = {
            "准确率": 准确, "基线": 0.25, "答对": 对, "答错": n - 对,
            "无效": sum(1 for d in 明细 if d["模型选"] == "?"),
            "样本数": n, "候选病案数": len(病案), "出题成功率": 出题率,
            "均耗时秒": round(耗时 / n, 1), "总耗时秒": round(耗时, 1),
            "来源": 源计,
            "明细": [d for d in 明细 if not d["对"]][:20],   # 只存答错的，方便复盘
        }
        con.execute("INSERT INTO 评测记录(时间,名称,样本数,指标) VALUES(?,?,?,?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "方剂四选一 · " + 标签来源名(),
                     n, json.dumps(指标, ensure_ascii=False)))
        con.commit()

        return {"ok": True, "名称": "方剂四选一 · " + 标签来源名(), "题数": n,
                "准确率": 准确, "基线": 0.25, "答对": 对,
                "出题成功率": 出题率, "均耗时秒": 指标["均耗时秒"],
                "来源": 源计, "指标": 指标}
    finally:
        con.close()


def main():
    条数 = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    出 = 跑(条数)
    if 出.get("错误"):
        print("× " + 出["错误"])
        return
    print("方剂四选一：%d 题，答对 %d，准确率 %.1f%%（随机基线 25%%）"
          % (出["题数"], 出["答对"], 出["准确率"] * 100))
    print("出题成功率 %.0f%%　来源 %s　均 %.1fs/题"
          % (出["出题成功率"] * 100, 出["来源"], 出["均耗时秒"]))
    print("\n答错的题（真方 ← 模型选）：")
    for d in 出["指标"]["明细"]:
        print("  案%-5s %-6s 真方 %-8s（覆盖 %d 味/%.2f）  模型选 %s"
              % (d["案id"], d["源"], d["真方"], d["覆盖"], d["覆盖率"], d["模型选"]))
    print("\n已写入 `评测记录` 表。")


if __name__ == "__main__":
    main()
