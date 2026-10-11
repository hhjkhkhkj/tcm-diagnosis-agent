# -*- coding: utf-8 -*-
"""看 Jev 和 DeepSeek 的结论一致性，跟正确率的关系"""
import json
import os
import re

SRC = r"D:\Download\医案集_OCR"
JEV = os.path.join(SRC, "_校准结果.jsonl")
DS = os.path.join(SRC, "_对照_deepseek.jsonl")


def canon(name):
    """合方顺序无关：A合B == B合A"""
    s = re.sub(r"[\s\[\]【】]", "", name)
    for a, b in [("菱", "姜"), ("荣胡", "柴胡"), ("乐胡", "柴胡"), ("棠胡", "柴胡"),
                 ("挂枝", "桂枝"), ("魔贯", "麻黄"), ("趣痰", "逐瘀"), ("渴中", "温中")]:
        s = s.replace(a, b)
    if "合" in s:
        s = "合".join(sorted(s.split("合")))
    return s


def load(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")]

j = load(JEV)
d = load(DS)
n = min(len(j), len(d))
print("样本 %d 条\n" % n)

both = j_only = d_only = none = 0
agree_hit = agree_n = dis_hit = dis_n = 0
rows = []
for i in range(n):
    a, b = j[i], d[i]
    ja = max(a["概率"], key=a["概率"].get)
    db = max(b["概率"], key=b["概率"].get)
    agree = (canon(ja) == canon(db))
    ha, hb = a["押中"], b["押中"]
    if agree:
        agree_n += 1
        agree_hit += 1 if ha else 0
    else:
        dis_n += 1
        dis_hit += 1 if ha else 0
    if ha and hb:
        both += 1; t = "两个都对"
    elif ha and not hb:
        j_only += 1; t = "只有 Jev 对"
    elif hb and not ha:
        d_only += 1; t = "只有 DS 对"
    else:
        none += 1; t = "两个都错"
    rows.append((i + 1, a["方"], ja, db, t, agree))

print("%-4s %-24s %-24s %-22s %-12s %s" % ("#", "正确方", "Jev 选", "DS 选", "结果", "一致?"))
print("-" * 104)
for r in rows:
    print("%-4d %-24s %-24s %-22s %-12s %s" % (r[0], r[1][:22], r[2][:22], r[3][:20], r[4], "✓同" if r[5] else ""))

print("\n" + "=" * 56)
print("【按两边结论是否一致 分组】")
print("  两边选同一个方 : %2d 条，其中 Jev 判对 %2d 条  ->  %.0f%%"
      % (agree_n, agree_hit, 100 * agree_hit / max(agree_n, 1)))
print("  两边分歧       : %2d 条，其中 Jev 判对 %2d 条  ->  %.0f%%"
      % (dis_n, dis_hit, 100 * dis_hit / max(dis_n, 1)))
print("\n【四分类】")
print("  两个都对      : %2d" % both)
print("  只有 Jev 对   : %2d" % j_only)
print("  只有 DS 对    : %2d" % d_only)
print("  两个都错      : %2d" % none)
print("\n  Jev 总: %d/%d = %.1f%%" % (both + j_only, n, 100 * (both + j_only) / n))
print("  DS  总: %d/%d = %.1f%%" % (both + d_only, n, 100 * (both + d_only) / n))
