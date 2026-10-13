# -*- coding: utf-8 -*-
"""给倪海厦 951 例医案抽「症状 / 舌脉」标签（模型）

为什么必须用模型：
  · 倪案原文是**繁体**（脈診／頻尿／雙足溫），词表却是简体（沉脉／细脉）——
    硬匹配一条都对不上；
  · 词表本身还带 OCR 错字（半夜会圆过来 / 长期整日全身偿怠）。
  模型能跨繁简、认出口语同义（「脈沉有力」→「沉脉」）。

为什么用 low 思考档 + 并发：
  deepseek-flash 默认跑 high（一例 55~85 秒），这里是「从固定表里挑词」，
  不需要推理，降到 low（一例 ~55 秒）再开线程池并发，整批才跑得完。

写回 医案.症状 / 医案.舌脉（JSON 数组）+ 抽取方式='模型'。
**幂等 / 可续跑**：只处理 症状 为空的行；网络请求在子线程，落库在主线程。

用法：
  py _抽倪标签.py            全部（并发 12）
  py _抽倪标签.py 8          只跑 8 例（试跑）
  py _抽倪标签.py 0 16       全部，并发 16
"""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

from 模型判断 import DS_KEY, DS_URL, DS_MODEL   # noqa: E402


def 取词表(con):
    """可选标签 = **归一后的规范因子**（从 `医案因子` 取）

    2026-10-13 改。原来读的是 `词表` 表那 444 条 —— 那是 `_抽标签.py`
    逐行切 OCR 的产物，带大量字符级错读（「活有齿痕」「半夜会圆过来」）。
    拿它当「可选标签」喂模型，**模型会照抄那些错字** ——
    倪案 951 例就是这么被污染的（「下肢魔真」「听力问庭」在两侧都出现）。

    现在改用归一后的因子：干净、就在库里、还自带「症状/舌脉」维度。
    整条链因此自洽：**词表来源 = 检索用的 = 归一后的**，一个源头。
    """
    d = {"舌脉": [], "症状": []}
    for 词, 维 in con.execute(
            "SELECT DISTINCT 因子,维度 FROM 医案因子 WHERE 维度 IN ('症状','舌脉')"):
        if 维 in d and 词 not in d[维]:
            d[维].append(词)
    if not (d["舌脉"] or d["症状"]):
        # **故意报错，不退回去读旧 `词表`** —— 静默退回脏表正是当初出事的模式
        raise SystemExit(
            "读不到归一后的因子（`医案因子` 表是空的？）。\n"
            "先跑 `py _重归标签.py` 生成它。\n"
            "**不要**退回读 `词表` 那 444 条 —— 那是带 OCR 错字的旧表，喂给模型会把错字抄回来。")
    return d


def 拼文本(r):
    段 = []
    if r["主诉"]:
        段.append("主诉：" + r["主诉"])
    if r["问诊"]:
        段.append("问诊：" + r["问诊"])
    if r["望诊"]:
        段.append("望诊：" + r["望诊"])
    if r["诊断"]:
        段.append("诊断：" + r["诊断"])
    return "\n".join(段)[:4000]


def 造提示(词, 文本):
    return ("下面是一位中医师对病人的四诊描述（原文为繁体）。"
            "请从【可选标签】里挑出这段话**确实包含**的标签。\n"
            "规则：只从给定标签里挑，不要自己造词；没提到的绝对不要挑；"
            "繁简与近义都要对上（例：\"脈沉有力\"→\"沉脉\"，"
            "\"舌苔白厚\"→\"舌苔白\"，\"兩尺弱\"→\"弱脉\"）。\n"
            '只输出 JSON：{"标签": ["…","…"]}，不要任何别的文字。\n\n'
            "【可选标签】\n" + "、".join(词) + "\n\n"
            "【四诊描述】\n" + 文本)


def 抽(词, 文本):
    """单例：返回标签列表（已按词表过滤），失败抛异常"""
    r = requests.post(
        DS_URL,
        headers={"Authorization": "Bearer " + DS_KEY, "Content-Type": "application/json"},
        json={"model": DS_MODEL, "messages": [{"role": "user", "content": 造提示(词, 文本)}],
              "reasoning_effort": "low",          # 挑词不需要推理，high 会慢一倍
              "response_format": {"type": "json_object"}},
        timeout=120)
    r.raise_for_status()
    出 = json.loads(r.json()["choices"][0]["message"]["content"]).get("标签", [])
    合法 = set(词)
    return [t for t in 出 if t in 合法]


def 处理一条(aid, 词, 文本):
    """子线程：带退避重试，返回 (id, 标签 or None)"""
    for 试 in range(4):
        try:
            return aid, 抽(词, 文本)
        except Exception as e:
            if 试 == 3:
                print("  id%s 失败：%r" % (aid, e), flush=True)
            time.sleep(2 * (试 + 1))
    return aid, None


def main():
    限 = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    并发 = int(sys.argv[2]) if len(sys.argv) > 2 else 12

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    词表 = 取词表(con)
    全部词 = 词表["舌脉"] + 词表["症状"]

    待 = [(r["id"], 拼文本(r)) for r in con.execute(
        "SELECT * FROM 医案 WHERE source='倪海厦' AND (症状 IS NULL OR 症状='') ORDER BY id")]
    if 限:
        待 = 待[:限]
    待 = [(i, t) for i, t in 待 if t.strip()]
    print("待抽 %d 例（词表 %d 词：舌脉%d / 症状%d，并发 %d）"
          % (len(待), len(全部词), len(词表["舌脉"]), len(词表["症状"]), 并发), flush=True)

    好 = 坏 = 无标签 = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=并发) as 池:
        任务 = {池.submit(处理一条, aid, 全部词, txt): aid for aid, txt in 待}
        for k, fut in enumerate(as_completed(任务), 1):
            aid, 标签 = fut.result()
            if 标签 is None:
                坏 += 1
            else:
                if not 标签:
                    无标签 += 1
                舌脉 = sorted(t for t in 标签 if any(x in t for x in "舌脉苔"))
                症状 = sorted(t for t in 标签 if t not in 舌脉)
                con.execute(
                    "UPDATE 医案 SET 症状=?, 舌脉=?, 抽取方式='模型' WHERE id=?",
                    (json.dumps(症状, ensure_ascii=False),
                     json.dumps(舌脉, ensure_ascii=False), aid))
                con.commit()
                好 += 1
            if k % 25 == 0 or k == len(待):
                print("  进度 %d/%d  用时%.0fs  均%.1fs/例  无标签%d  失败%d"
                      % (k, len(待), time.time() - t0, (time.time() - t0) / k,
                         无标签, 坏), flush=True)

    print("\n完成：成功 %d / 失败 %d / 抽到零标签 %d"
          % (好, 坏, 无标签), flush=True)
    con.close()


if __name__ == "__main__":
    main()
