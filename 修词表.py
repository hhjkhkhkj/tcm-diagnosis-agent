# -*- coding: utf-8 -*-
"""修词表 —— 从 OCR 原文重建干净的受控词表（**零 token，不调模型**）

为什么要修（2026-10-13 实测）：
  库里 `词表` 那 444 条是 `_抽标签.py` 逐行切「辨证论治」块的结果 —— 切法没错
  （原文确实一行一个），但**手机拍照 143dpi，字符级不可靠**：
  「舌有齿痕」在某页读成「活有齿痕」，「半夜会醒过来」读成「半夜会圆过来」。
  更糟的是**同一则医案在书里被 OCR 了两遍**，每种写法各数一次频次，
  于是同一个症状裂成好几条 → 444 里 228 条只出现一次。

  好消息：正确写法在全书里出现**很多很多次**，乱码只是个别页。
  → 关键是**频次怎么数**：只数「辨证论治块那一行」会数出个位数，
     正确的和乱码的难分伯仲（`v1` 就是这么把「容易焦躁紧张」输给「容易焦曦紧张」的）。
     **改成数「这个字符串在全书 547 页里出现了多少次」**，正确写法的优势立刻拉开。

两个安全阀（别省）：
  ① **反义字保护**：纯按字形相似度会把「喜冷饮」并到「喜热饮」、「舌苔白」并到「舌苔黄」。
  ② **插入/删除保护**：把「舌质红」并到「舌质淡红」也是错的 —— 修的是**多出/少了**一个词，
     不是替换掉一个字。所以「淡/深/浓/微/略/极/甚/无/不/未/有」这类**程度与否定字**，
     一旦出现在差集里就一律不并。
  拿不准一律**不并**，扔进待审清单给人看 —— 宁可不并，不可并错。

产出：
  _词表_新.tsv   规范词 / 全书频次 / 是否锚 / 别名
  _词表_待审.md  被并掉的变体 + 只出现一两次的规范词

用法：py _修词表.py
"""
import glob
import os
import re
from collections import Counter, defaultdict

from opencc import OpenCC

SRC = os.path.dirname(os.path.abspath(__file__))
简 = OpenCC("t2s")

阈值长 = 0.75     # 长度≥5 的词
阈值短 = 0.90     # 长度≤4 的词 —— 短词差一个字含义天差地别，卡死

反义对 = ["冷热", "白黄", "干溏", "多少", "大小", "浮沉", "数迟", "厚薄", "虚实",
          "滑涩", "红白", "胖瘦", "痛痒", "上下", "前后", "内外", "来去", "硬软",
          "深浅", "轻重", "高低", "长短", "始终", "寒热", "燥湿"]
反义字 = {}
for _对 in 反义对:
    for _ch in _对:
        反义字.setdefault(_ch, set()).update(_对)

# 程度 / 否定字：多一个少一个都变意思，出现在差集里就不并
保护单字 = set("淡深浓微略极甚无不未有各兼复再更特别较稍微")

体质名 = {"肾气虚", "肾阳虚", "肾气不固", "阳虚", "脾阳虚", "脾气虚", "心肺气虚",
          "气虚", "血虚", "阴虚", "肝郁", "痰湿", "湿热", "血瘀"}

RE_头 = re.compile(r"辨[证辩]论治")
RE_止 = re.compile(r"[\[【]\s*方名|中医大脑：")
RE_体质段 = re.compile(r"脉症与体质的关联|整体体质|体质分析")
RE_切 = re.compile(r"[，,、－\-–—·．.；;／/…【】\[\]\s]+")
RE_洗 = re.compile(r"[\s，,、；;：:·．.－\-–—／/【】\[\]()（）]+")


def 洗干净(s):
    return RE_洗.sub("", 简.convert(s or ""))


def lcs(a, b):
    """真·最长公共子序列（不是最长连续块 —— 中间坏一个字要能对上）"""
    m, n = len(a), len(b)
    if not m or not n:
        return 0
    前 = [0] * (n + 1)
    for i in range(1, m + 1):
        ai = a[i - 1]
        当 = [0] * (n + 1)
        for j in range(1, n + 1):
            当[j] = 前[j - 1] + 1 if ai == b[j - 1] else max(前[j], 当[j - 1])
        前 = 当
    return 前[n]


def 相似(a, b):
    return lcs(a, b) / max(len(a), len(b))


def 冲突(a, b):
    """能不能并？返回 True = 不许并"""
    ca, cb = Counter(a), Counter(b)
    差 = set()
    for ch in set(ca) | set(cb):
        if ca[ch] != cb[ch]:
            差.add(ch)
    # 替换：差集两边的字数相等且都在保护里 → 反义
    if len(差) >= 2 and all(ch in 反义字 for ch in 差):
        return True
    # 插入 / 删除：差集里出现程度字或否定字
    if 差 & 保护单字:
        return True
    return False


def 载全文():
    全 = []
    for fp in sorted(glob.glob(os.path.join(SRC, "*", "p*.txt"))):
        try:
            全.append(open(fp, encoding="utf-8").read())
        except Exception:
            pass
    return 简.convert("\n".join(全))


def 收候选(文):
    """候选词从哪儿来：辨证论治块逐行 + 体质段按标点切 + 已在库/受控表里的"""
    频 = Counter()
    for fp in sorted(glob.glob(os.path.join(SRC, "*", "p*.txt"))):
        try:
            页文 = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        m = RE_头.search(页文)
        if m:
            tail = 页文[m.end():]
            cut = len(tail)
            for mm in RE_止.finditer(tail):
                cut = min(cut, mm.start())
            for ln in tail[:cut].split("\n"):
                s = 洗干净(ln)
                if 2 <= len(s) <= 12 and not re.search(r"\d", s):
                    频[s] += 1
        # ② 体质段：**按行取**，只留像「标签串」的行 —— 散文行一律丢掉。
        #    （v2 用字符窗口抓，抓进来一堆「请参照前文。」「南方医科大学…」这种句子）
        for m2 in RE_体质段.finditer(页文):
            seg = 页文[m2.end(): m2.end() + 1500]
            for ln in seg.split("\n"):
                ln = re.sub(r"^[【\[][^】\]]*[】\]]", "", ln.strip())   # 去掉【整体体质】这类标记
                if not ln or len(ln) > 40:
                    continue
                if re.search(r"[。？！；：」』《》〈〉()（）\"'“”]", ln):
                    continue
                for piece in RE_切.split(ln):
                    s = 洗干净(piece)
                    if 2 <= len(s) <= 12 and not re.search(r"\d", s) and s not in 体质名:
                        频[s] += 1
    return 频


def 锚词():
    s = set()
    fp = os.path.join(SRC, "_受控词表.txt")
    if os.path.exists(fp):
        for ln in open(fp, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#") or ln.startswith("["):
                continue
            w = 洗干净(ln.split("\t")[0])
            if w:
                s.add(w)
    return s


def main():
    文 = 载全文()
    候选 = 收候选(文)

    # 库里已有的标签也当候选参加投票
    import sqlite3
    db = os.path.join(SRC, "中医大脑医案库.db")
    if os.path.exists(db):
        con = sqlite3.connect(db)
        for (w,) in con.execute("select 词 from 词表"):
            w = 洗干净(w)
            if 2 <= len(w) <= 12 and w not in 候选:
                候选[w] = 1

    锚 = 锚词()
    词集 = set(候选) | 锚

    # 频次 = 该字符串在**全书**里出现多少次（这是关键修正）
    全书频 = {w: 文.count(w) for w in 词集}

    print("候选词 %d 个（其中人工锚 %d 个）" % (len(词集), len(锚)))
    print("全书频次 ≥5 的：%d 个 ／ 只出现 1 次的：%d 个"
          % (len([w for w in 词集 if 全书频[w] >= 5]),
             len([w for w in 词集 if 全书频[w] <= 1])))

    簇 = {}
    别名 = defaultdict(list)
    头表 = []
    for w in sorted(词集, key=lambda x: (-(x in 锚), -全书频[x], -len(x))):
        if w in 簇:
            continue
        th = 阈值短 if len(w) <= 4 else 阈值长
        for h in 头表:
            if w == h or 冲突(w, h):
                continue
            if 相似(w, h) >= th:
                簇[w] = h
                别名[h].append(w)
                break
        else:
            簇[w] = w
            头表.append(w)

    头表.sort(key=lambda h: -全书频[h])
    压掉 = len(词集) - len(头表)
    print("归并后 规范词 %d 个（压掉 %d 个变体）" % (len(头表), 压掉))

    with open(os.path.join(SRC, "_词表_新.tsv"), "w", encoding="utf-8") as f:
        f.write("规范词\t全书频次\t是否锚\t别名\n")
        for h in 头表:
            f.write("%s\t%d\t%s\t%s\n"
                    % (h, 全书频[h], "★" if h in 锚 else "", " / ".join(sorted(set(别名[h])))))

    少 = [h for h in 头表 if 全书频[h] <= 2]
    with open(os.path.join(SRC, "_词表_待审.md"), "w", encoding="utf-8") as f:
        f.write("# 待审清单（%d 个规范词）\n\n"
                "## 一、被并掉的变体 —— 抽查有没有并错\n\n" % len(头表))
        for h in 头表:
            v = sorted(set(别名[h]))
            if v:
                f.write("- **%s** (%d) ← %s\n" % (h, 全书频[h], "、".join(v)))
        f.write("\n## 二、全书只出现 ≤2 次的规范词（%d 个）\n\n"
                "可能是真·罕见症状，也可能是没救回来的残留乱码 —— **需要你扫一眼**。\n\n" % len(少))
        f.write("、".join("%s(%d)" % (h, 全书频[h]) for h in 少) + "\n")

    print("\n写了 _词表_新.tsv / _词表_待审.md")
    print("\n=== 并掉变体最多的 15 个簇 ===")
    for h in sorted(头表, key=lambda x: -len(set(别名[x])))[:15]:
        v = sorted(set(别名[h]))
        if v:
            print("  %-16s (%d) ← %s" % (h, 全书频[h], "、".join(v[:6])))


if __name__ == "__main__":
    main()
