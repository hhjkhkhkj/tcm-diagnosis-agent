# -*- coding: utf-8 -*-
"""抽因子 —— 模型从病案原文里抽 六经 / 体质 / 治法 / 病程

写进 T18 `医案因子`（维度 = 六经|体质|治法|病程）。**幂等**：按维度先删后插。

## 这个脚本唯一重要的设计：区分「原文说的」和「模型推的」

每条抽出来的因子都带一个 `依据`：
    原文     病案文字里**明确写了**（例：「病人從少陽證走到太陽證」→ 六经少阳、太阳）
    模型推断  病案没写，模型根据症状推出来的

**为什么这条不能省**：第二阶段拿「六经」当结果变量（Y）来算判别力时，
**只有 `依据=原文` 的那批能用**。拿模型推断出来的六经去验证「症状→六经」的判据，
等于**自己拟合自己** —— 算出来的不是医学规律，是模型的口味。

同理，`证据` 里抄回原话，是为了能追回页码。**追不回去的不许进框架。**

## 跟上午那批的差别（省钱）
  · **不塞整份 444 词词表** —— 六经就 6 个值、体质就那十几个，硬编码进提示即可。
    上午每次请求白搬了 ~1000 token 的候选词。
  · **打用量** —— 跑完打印 prompt/completion token 总数，钱看得见。

用法：
    py _抽因子.py          全部
    py _抽因子.py 5        只跑 5 例（冒烟）
    py _抽因子.py 5 8      5 例、并发 8
"""
import json
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from opencc import OpenCC

简 = OpenCC("t2s").convert

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "中医大脑医案库.db")
sys.path.insert(0, HERE)

# 抽取层是**独立的一条线**（跟判断层分开）—— 定义在 模型判断.py，值在 config.py
from 模型判断 import 抽取_KEY, 抽取_URL, 抽取_MODEL   # noqa: E402
抽取_模型 = 抽取_MODEL

六经值 = ["太阳", "阳明", "少阳", "太阴", "少阴", "厥阴"]
体质值 = ["肾气虚", "肾阳虚", "肾气不固", "阳虚", "脾阳虚", "脾气虚", "心肺气虚",
          "气虚", "血虚", "阴虚", "血瘀", "痰湿", "湿热", "肝郁", "胃火旺", "心脾两虚"]
维度表 = ["六经", "体质", "治法", "病程"]

提示头 = (
    "下面是一位中医师（倪海厦）的病案，原文是**繁体**。请从中判读四项，"
    "并且**严格区分「原文自己说的」和「你推断的」**。\n\n"
    "① **六经**：只能取 " + "、".join(六经值) + "（可多选；没提到就空数组）\n"
    "② **体质**：只能取 " + "、".join(体质值) + "（可多选；没提到就空数组）\n"
    "③ **治法**：一两句话，不超过 12 字（例：温阳利水 / 和解少阳）\n"
    "④ **病程**：只能取 新病 / 久病\n\n"
    "**每一项都必须给这两个字段**：\n"
    "   依据 = \"原文\"  病案文字里**明确写了**（例：「病人從少陽證走到太陽證」→ 六经=少阳、太阳，依据=原文）\n"
    "   依据 = \"推断\"  病案没写，是你根据症状推出来的\n"
    "   原话 = 抄下支持它的那**一小段原文**；依据=推断 的写 \"无\"\n\n"
    "宁可标「推断」也不要硬凑「原文」——这个标记后面要拿来判真假，标错了整个结论作废。\n\n"
    "只输出 JSON，形如：\n"
    '{"六经":[{"值":"少阳","依据":"原文","原话":"病人從少陽證…"}],'
    '"体质":[{"值":"肾阳虚","依据":"推断","原话":"无"}],'
    '"治法":[{"值":"和解少阳","依据":"推断","原话":"无"}],'
    '"病程":[{"值":"久病","依据":"推断","原话":"无"}]}\n\n'
    "四项都要出现，没有内容的写空数组 []。\n"
)


def 取文本(r):
    """给模型看的病案正文 —— 用结构化字段拼，控制在 3000 字内"""
    段 = []
    for k in ("主诉", "诊断", "转归", "问诊"):
        v = (r[k] or "").strip()
        if v:
            段.append("【%s】%s" % (k, v))
    全 = (r["全文"] or "").strip()
    if 全:
        段.append("【全文】" + 全)
    return "\n".join(段)[:3000]


def 抽(文本):
    r = requests.post(
        抽取_URL,
        headers={"Authorization": "Bearer " + 抽取_KEY, "Content-Type": "application/json"},
        json={"model": 抽取_模型,
              "messages": [{"role": "user", "content": 提示头 + "\n【病案】\n" + 文本}],
              "temperature": 0,          # 定死采样：抽取是「照抄」，不该每次给我不同的答案
              "reasoning_effort": "low",
              "response_format": {"type": "json_object"}},
        timeout=180)
    r.raise_for_status()
    d = r.json()
    用 = d.get("usage") or {}
    return (json.loads(d["choices"][0]["message"]["content"]),
            int(用.get("prompt_tokens") or 0), int(用.get("completion_tokens") or 0))


def 处理一条(aid, 文本):
    """带退避重试"""
    for 试 in range(4):
        try:
            return (aid,) + 抽(文本)
        except Exception as e:
            if 试 == 3:
                print("  id%s 失败：%r" % (aid, e), flush=True)
            time.sleep(2 * (试 + 1))
    return aid, None, 0, 0


def 拆行(出, aid):
    """把模型输出摊成 医案因子 的行；只收白名单里的值"""
    行 = []
    if not isinstance(出, dict):
        return 行
    for 维 in 维度表:
        for 项 in (出.get(维) or []):
            if isinstance(项, str):
                项 = {"值": 项}
            if not isinstance(项, dict):
                continue
            值 = 简(str(项.get("值") or "").strip())
            if not 值:
                continue
            # 白名单检查必须在繁简归一**之后** —— 倪案原文是繁体，模型有时照抄
            # （输出「陽明」会被白名单丢掉），所以先转简再判
            if 维 == "六经" and 值 not in 六经值:
                continue
            if 维 == "体质" and 值 not in 体质值:
                continue
            if 维 == "病程" and 值 not in ("新病", "久病"):
                continue
            依 = str(项.get("依据") or "").strip()
            来源 = "原文" if 依 in ("原文", "原文陈述") else "模型推断"
            证 = str(项.get("原话") or "").strip()[:200]
            行.append((aid, 维, 值, 值, 来源, 证, 1.0 if 来源 == "原文" else 0.6))
    return 行


def main():
    限 = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    并发 = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    查 = "SELECT id,主诉,诊断,转归,问诊,全文 FROM 医案 WHERE source='倪海厦' ORDER BY id"
    待 = []
    for r in con.execute(查):
        t = 取文本(r)
        if t:
            待.append((r["id"], t))
        if 限 and len(待) >= 限:
            break

    print("待抽 %d 例，并发 %d" % (len(待), 并发), flush=True)
    print("抽取模型 %s @ %s" % (抽取_模型, 抽取_URL.split("//")[-1].split("/")[0]), flush=True)
    行 = []
    入量 = 出量 = 0
    起 = time.time()
    完成 = 0
    with ThreadPoolExecutor(max_workers=并发) as 池:
        任务 = {池.submit(处理一条, aid, t): aid for aid, t in 待}
        for fu in as_completed(任务):
            aid, 出, pi, co = fu.result()
            入量 += pi
            出量 += co
            if 出:
                行.extend(拆行(出, aid))
            完成 += 1
            if 完成 % 50 == 0:
                print("  进度 %d/%d  用时%ds  入%d/出%d token"
                      % (完成, len(待), time.time() - 起, 入量, 出量), flush=True)

    # 保险：失败太多就**不写库**。否则「先删后插」会把上一轮的成果整个清空。
    # （2026-10-13 踩过：百炼欠费，全部返回 400 Arrearage，眼看要清掉 951 例的结果）
    if len(行) < max(1, len(待)) * 0.5:
        print("\n[!] 只抽到 %d 行 / %d 例，失败太多 —— **没有写库**，上一轮的成果保住了。"
              % (len(行), len(待)))
        print("    常见原因：账号欠费（百炼会返回 400 / type=Arrearage）。查余额后再跑。")
        con.close()
        return

    # 幂等：**只清这批跑过的案子**，不是整维度全清 ——
    # 否则跑一次「10 例冒烟」会把前一次 951 例的结果整个冲掉。
    ids = [a for a, _ in 待]
    for i in range(0, len(ids), 500):
        块 = ids[i:i + 500]
        标 = ",".join("?" * len(块))
        con.execute("DELETE FROM 医案因子 WHERE 维度 IN ('六经','体质','治法','病程')"
                    " AND 医案id IN (%s)" % 标, 块)
    con.executemany("INSERT INTO 医案因子(医案id,维度,因子,原词,来源,证据,置信度)"
                    " VALUES(?,?,?,?,?,?,?)", 行)
    con.commit()

    print("\n完成：%d 例 / 用时 %.0f 秒" % (完成, time.time() - 起))
    print("写进 医案因子 %d 行" % len(行))
    print("**用量：input %d tok ／ output %d tok ／ 合计 %.2f 万 tok**"
          % (入量, 出量, (入量 + 出量) / 10000))
    for 维 in 维度表:
        n = con.execute("SELECT COUNT(*) FROM 医案因子 WHERE 维度=?", (维,)).fetchone()[0]
        原 = con.execute("SELECT COUNT(*) FROM 医案因子 WHERE 维度=? AND 来源='原文'",
                        (维,)).fetchone()[0]
        print("  %-4s %5d 条  其中 依据=原文 %d 条（这批才能当结果变量）" % (维, n, 原))
    con.close()


if __name__ == "__main__":
    main()
