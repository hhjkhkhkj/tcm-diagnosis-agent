# -*- coding: utf-8 -*-
"""
从医案集 OCR 文本里抽样本：四诊 -> 方名

每页结构（OCR 后）：
    辨证论治
      <症状行...>
      <舌象行...>
      <脉象行...>
    【方名】XXX
      <药味:剂量...>

输出：D:\\Download\\医案集_OCR\\_样本.jsonl
"""
import glob
import json
import os
import re

SRC = r"D:\Download\医案集_OCR"
OUT = os.path.join(SRC, "_样本.jsonl")

RE_HEAD = re.compile(r"辨[证辩]论治")
RE_FANG = re.compile(r"[\[【]\s*方名\s*[\]】]\s*(.+)")
RE_YAO = re.compile(r"^(.{1,6}?)[:：]\s*([\d.]+)\s*g\s*$")
RE_SHI = re.compile(r"^[\u4e00-\u9fa5]{2,8}(脉|苔|舌)$|^(舌|脉)")


def parse(text):
    m = RE_HEAD.search(text)
    if not m:
        return None
    tail = text[m.end():]

    mf = RE_FANG.search(tail)
    if not mf:
        return None
    fang = mf.group(1).strip()
    fang = re.sub(r"[\[\]【】]", "", fang).strip()
    if not fang or "大数据组方" in fang:
        return None                      # 没对上经典方，跳过
    if len(fang) < 3 or len(fang) > 30:
        return None

    # 方名之前的是四诊
    block = tail[: mf.start()]
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    lines = [l for l in lines if not l.startswith("中医大脑") and "问止" not in l]
    if len(lines) < 4:
        return None

    舌脉 = [l for l in lines if RE_SHI.match(l)]
    症状 = [l for l in lines if l not in 舌脉]

    # 方名之后的药味
    after = tail[mf.end():]
    药 = []
    for l in after.split("\n")[:25]:
        l = l.strip()
        mm = RE_YAO.match(l)
        if mm:
            药.append(mm.group(1).strip() + mm.group(2) + "g")
        elif 药 and not RE_YAO.match(l):
            if len(药) >= 3:
                break

    return {"方": fang, "症状": 症状, "舌脉": 舌脉, "药": 药}


def main():
    seen = {}
    for fp in sorted(glob.glob(os.path.join(SRC, "*", "p*.txt"))):
        try:
            text = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        r = parse(text)
        if not r:
            continue
        r["页"] = os.path.relpath(fp, SRC)
        key = (r["方"], tuple(r["症状"][:3]))
        if key in seen:
            continue                 # 同一则医案的复诊页，去重
        seen[key] = r

    rows = list(seen.values())
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("抽出 %d 条样本 -> %s\n" % (len(rows), OUT))
    for r in rows:
        print("─" * 58)
        print("方 :", r["方"])
        print("舌脉:", "、".join(r["舌脉"]))
        print("症状:", "、".join(r["症状"][:12]))
        print("药 :", "、".join(r["药"][:10]), ("…共%d味" % len(r["药"])) if len(r["药"]) > 10 else "")


if __name__ == "__main__":
    main()
