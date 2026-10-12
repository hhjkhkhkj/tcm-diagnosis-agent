# -*- coding: utf-8 -*-
"""
三方交叉验证 —— 给一个病案，三个独立的判断源各说一遍

  [检索] 数据库里最相似的旧医案 + 它们用的方   ← 不靠模型，靠数据
  [主判] Jev                                 ← 概率
  [复核] DeepSeek                            ← 独立再判一次

三方一致 -> 高可信；有分歧 -> 标红「需人工复核」

用法:
    py _三方验证.py                   跑库里的第 1 条做演示
    py _三方验证.py 3                 跑第 3 条
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter

import requests

SRC = r"D:\Download\医案集_OCR"
DB = os.path.join(SRC, "中医大脑医案库.db")

# 密钥与模型调用统一从 模型判断.py 拿（密钥在 config.py 里，那个文件不进仓库）
from 模型判断 import (JEV_KEY, JEV_URL, JEV_MODEL,      # noqa: F401
                      DS_KEY, DS_URL, DS_MODEL, canon)  # noqa: F401


# ---------- ① 数据库检索 ----------
def 查库(症状, 舌脉, 排除id=None, topn=3):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    我 = set(症状) | set(舌脉)
    out = []
    for r in con.execute("SELECT * FROM 医案"):
        if 排除id is not None and r["id"] == 排除id:
            continue                      # 不能把病案自己检索出来
        his = set(json.loads(r["症状"])) | set(json.loads(r["舌脉"]))
        if not his:
            continue
        交集 = 我 & his
        # 舌脉权重更高（脉是独有维度）
        权重 = len(交集) + len(我 & set(json.loads(r["舌脉"]))) * 1.5
        jac = len(交集) / max(len(我 | his), 1)
        out.append({"相似度": round(jac, 3), "重叠": len(交集), "加权": round(权重, 1),
                    "方名": r["方名"], "页": "%s p%d" % (r["pdf"][-6:], r["页"]),
                    "共有": sorted(交集)})
    con.close()
    out.sort(key=lambda x: -x["加权"])
    return out[:topn]


# ---------- ② Jev ----------
def 问jev(症状, 舌脉, 候选):
    state = ("患者四诊信息：\n症状：" + "、".join(症状) +
             "\n舌象：" + "、".join(x for x in 舌脉 if "脉" not in x) +
             "\n脉象：" + "、".join(x for x in 舌脉 if "脉" in x))
    qs = {}
    for i, o in enumerate(候选):
        qs["o%d" % i] = {"type": "noul",
                         "instructions": "这位患者是中医门诊病案。根据四诊信息，"
                                         "这个病案是否应当选用「%s」？符合适应证判「是」，否则判「否」。" % o}
    r = requests.post(JEV_URL, headers={"Authorization": "Bearer " + JEV_KEY,
                                        "Content-Type": "application/json"},
                      json={"model": JEV_MODEL, "state": state, "questions": qs}, timeout=90)
    r.raise_for_status()
    a = r.json()["answers"]
    return {候选[i]: a["o%d" % i]["noul"] for i in range(len(候选))}


# ---------- ③ DeepSeek ----------
def 问ds(症状, 舌脉, 候选):
    state = ("症状：" + "、".join(症状) +
             "\n舌象：" + "、".join(x for x in 舌脉 if "脉" not in x) +
             "\n脉象：" + "、".join(x for x in 舌脉 if "脉" in x))
    p = ("你是中医经方专家。根据四诊信息，给每个候选方打 0~100 的匹配分。\n"
         "只输出 JSON：{\"方名\": 分数}，不要别的字。\n\n" + state +
         "\n\n候选方：\n" + "\n".join("- " + o for o in 候选))
    r = requests.post(DS_URL, headers={"Authorization": "Bearer " + DS_KEY,
                                       "Content-Type": "application/json"},
                      json={"model": DS_MODEL, "messages": [{"role": "user", "content": p}],
                            "temperature": 0, "response_format": {"type": "json_object"}}, timeout=120)
    r.raise_for_status()
    d = json.loads(r.json()["choices"][0]["message"]["content"])
    return {canon(k): float(v) for k, v in d.items()}


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM 医案 ORDER BY 篇, 页").fetchall()
    case = rows[idx - 1]
    症状 = json.loads(case["症状"])
    舌脉 = json.loads(case["舌脉"])
    真方 = case["方名"]
    con.close()

    print("=" * 66)
    print("病案 #%d   出处 %s p%d   篇：%s" % (idx, case["pdf"][-6:], case["页"], case["篇"]))
    print("=" * 66)
    print("症状：", "、".join(症状))
    print("舌脉：", "、".join(舌脉))
    print("【书上真开的方】", 真方)

    # ① 查库
    print("\n" + "─" * 66)
    print("① 数据库检索（找最相似的旧案）")
    相似 = 查库(症状, 舌脉, 排除id=case["id"])
    for s in 相似:
        print("   相似度 %.2f  加权 %.1f  →  %-24s (%s)" % (s["相似度"], s["加权"], s["方名"], s["页"]))
        print("      共有：%s" % "、".join(s["共有"][:8]))

    # 候选：真方 + 库里最相似的 3 个别的方
    候选 = [真方]
    for s in 相似:
        if canon(s["方名"]) not in [canon(c) for c in 候选] and "组方" not in s["方名"]:
            候选.append(s["方名"])
    while len(候选) < 4:
        for r in rows:
            if canon(r["方名"]) not in [canon(c) for c in 候选] and "组方" not in r["方名"]:
                候选.append(r["方名"])
                break
    候选 = 候选[:4]

    # ② Jev
    print("\n" + "─" * 66)
    print("② Jev")
    try:
        jp = 问jev(症状, 舌脉, 候选)
        for k, v in sorted(jp.items(), key=lambda x: -x[1]):
            print("   %5.1f%%  %s" % (v * 100, k))
        jev_pick = max(jp, key=jp.get)
    except Exception as e:
        print("   失败:", str(e)[:90])
        jp, jev_pick = {}, None

    # ③ DeepSeek
    print("\n" + "─" * 66)
    print("③ DeepSeek")
    try:
        dp = 问ds(症状, 舌脉, 候选)
        for k, v in sorted(dp.items(), key=lambda x: -x[1]):
            print("   %5.0f    %s" % (v, k))
        ds_pick = max(dp, key=dp.get)
    except Exception as e:
        print("   失败:", str(e)[:90])
        dp, ds_pick = {}, None

    # 汇总
    print("\n" + "=" * 66)
    票 = [canon(真方)]
    if jp:
        票.append(canon(jev_pick))
    if dp:
        票.append(canon(ds_pick))
    print("书上真方   :", 真方)
    print("Jev       :", jev_pick, " %s" % ("✓" if canon(jev_pick or "") == canon(真方) else "✗") if jev_pick else "")
    print("DeepSeek  :", ds_pick, " %s" % ("✓" if canon(ds_pick or "") == canon(真方) else "✗") if ds_pick else "")
    if jev_pick and ds_pick:
        一致 = canon(jev_pick) == canon(ds_pick)
        print("\n>>> 两个模型 %s  →  %s" % ("一致" if 一致 else "分歧",
                                          "高可信" if 一致 else "⚠️ 需人工复核"))


if __name__ == "__main__":
    main()
