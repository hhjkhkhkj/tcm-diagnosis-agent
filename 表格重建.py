# -*- coding: utf-8 -*-
"""
表格页重建 v1 —— 只处理「药对分析」页

问题：正文 OCR 把三列表格读成了流水文本，列对应关系断了。

做法：重跑这些页的 OCR，**保留坐标**，用「表头行」定列边界：
        找到 药对 / 主治 / 应用 三个表头单元格 -> 用它们的 x 范围当列边界
        表头以下的所有框，按 x 中心分列、按 y 重叠分行
      -> 输出结构化 JSON + 人看的 markdown

用法：
    py 表格重建.py test     只跑 1 页，打印过程（先验证再用）
    py 表格重建.py          跑全部
"""
import glob
import io
import json
import os
import re
import sys
import time

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

OCR_ROOT = r"D:\Download\医案集_OCR"
PDF_ROOT = r"D:\Download"
BOT = 0.92
SIDE = 0.86
DPI = 144

# OCR 出的页 目录名 -> 原 PDF
PDF_OF = {
    "图片转pdf_20261011_144227": "图片转pdf_20261011_144227.pdf",
    "图片转pdf_20261011_144659": "图片转pdf_20261011_144659.pdf",
    "图片转pdf_20261011_145759": "图片转pdf_20261011_145759.pdf",
    "图片转pdf_20261011_150118": "图片转pdf_20261011_150118.pdf",
    "图片转pdf_20261011_150457": "图片转pdf_20261011_150457.pdf",
    "图片转pdf_20261011_150728": "图片转pdf_20261011_150728.pdf",
}

KEYS = ["药对", "单味药"]        # 「行钥匙」列的候选
FIXED = ["主治", "应用"]         # 固定两列
HEADERS = KEYS + FIXED


def find_pages(keyword):
    out = []
    for fp in sorted(glob.glob(os.path.join(OCR_ROOT, "*", "p*.txt"))):
        try:
            t = open(fp, encoding="utf-8").read()
        except Exception:
            continue
        if keyword in t:
            d = os.path.basename(os.path.dirname(fp))
            p = int(re.search(r"p(\d+)\.txt", fp).group(1))
            out.append((d, p))
    return out


def ocr_page(doc, idx, ocr):
    pix = doc[idx].get_pixmap(dpi=DPI)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    w, h = img.size
    img = img.crop((0, 0, w, int(h * BOT)))
    w2 = img.size[0]
    arr = np.array(img)
    del pix
    img.close()
    res, _ = ocr(arr)
    del arr
    boxes = []
    if res:
        for box, txt, score in res:
            x0 = min(p[0] for p in box)
            x1 = max(p[0] for p in box)
            y0 = min(p[1] for p in box)
            y1 = max(p[1] for p in box)
            if x0 > SIDE * w2:
                continue
            boxes.append({"x0": x0, "x1": x1, "y0": y0, "y1": y1,
                          "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2, "t": txt})
    return boxes


def _header_rows(boxes):
    """找出页面上所有「钥匙列(药对/单味药) + 主治 + 应用」同 y 带的表头行"""
    found = {}
    for b in boxes:
        t = b["t"].replace(" ", "")
        if t in HEADERS:
            found.setdefault(t, []).append(b)
    out = []
    for key in KEYS:
        for bk in found.get(key, []):
            row = {key: bk}
            ok = True
            for h in FIXED:
                cand = [x for x in found.get(h, []) if abs(x["cy"] - bk["cy"]) < 60]
                if not cand:
                    ok = False
                    break
                row[h] = min(cand, key=lambda x: abs(x["cy"] - bk["cy"]))
            if ok:
                out.append((sum(v["cy"] for v in row.values()) / 3, row))
    out.sort()
    return out


def build_tables(boxes):
    """一张页面上可能有多张表 -> 返回列表"""
    heads = _header_rows(boxes)
    if not heads:
        return []

    tables = []
    for hi, (hy, hrow) in enumerate(heads):
        # 本表的范围：这个表头 到 下一个表头（或页尾）
        y_end = heads[hi + 1][0] - 60 if hi + 1 < len(heads) else 1e9
        region = [b for b in boxes if b["cy"] > hy + 5 and b["cy"] < y_end]
        if not region:
            continue

        cols = sorted(((h, hrow[h]["x0"], hrow[h]["x1"]) for h in hrow), key=lambda c: c[1])
        centers = [(c[0], (c[1] + c[2]) / 2) for c in cols]
        key_name = cols[0][0]                      # 最左那一列 = 行钥匙

        # 分行：第一列的格子定行
        keys = [b for b in region if _nearest(b["cx"], centers) == key_name]
        keys.sort(key=lambda b: b["cy"])
        if not keys:
            continue
        # 药对名换行时会拆成两格（上一格以 + 或 、 结尾 = 还没写完），要合并
        groups = []
        for b in keys:
            if groups and groups[-1][-1]["t"].rstrip().endswith(("+", "＋", "、", "加")):
                groups[-1].append(b)
            else:
                groups.append([b])
        keyrows = [{"t": "".join(x["t"].rstrip() for x in g),
                    "cy": g[-1]["cy"], "ids": [id(x) for x in g]} for g in groups]

        gaps = [keyrows[i + 1]["cy"] - keyrows[i]["cy"] for i in range(len(keyrows) - 1)]
        med = sorted(gaps)[len(gaps) // 2] if gaps else 300
        # 末行不设限的话会把表格下面的正文吃进来 -> 只到 最后一行起点 + 2.5 倍行距
        last_end = min(y_end, keyrows[-1]["cy"] + med * 2.5)

        bounds = ([hy + 5]
                  + [(keyrows[i]["cy"] + keyrows[i + 1]["cy"]) / 2 for i in range(len(keyrows) - 1)]
                  + [last_end])

        rows = [{"key": k["t"], "cells": {}} for k in keyrows]
        for b in region:
            ri = None
            for i, k in enumerate(keyrows):
                if bounds[i] <= b["cy"] < bounds[i + 1]:
                    ri = i
                    break
            if ri is None:
                continue
            if id(b) in keyrows[ri]["ids"]:
                continue                       # 自己就是行钥匙
            cn = _nearest(b["cx"], centers)
            rows[ri]["cells"].setdefault(cn, []).append(b["t"])

        tables.append({"cols": [c[0] for c in cols], "key_col": key_name,
                       "rows": [{"key": r["key"], "cells": {k: " ".join(v) for k, v in r["cells"].items()}}
                                for r in rows]})
    return tables


def _nearest(cx, centers):
    return min(centers, key=lambda c: abs(cx - c[1]))[0]


def build_table(boxes):
    t = build_tables(boxes)
    return t[0] if t else None


def main():
    only_one = len(sys.argv) > 1 and sys.argv[1] == "test"
    pages = find_pages("药对分析") + find_pages("单味药分析")
    pages = sorted(set(pages))
    if only_one:
        # 允许 py 表格重建.py test <目录号> <页号>
        if len(sys.argv) >= 4:
            d = [k for k in PDF_OF if sys.argv[2] in k][0]
            pages = [(d, int(sys.argv[3]))]
        else:
            pages = pages[:1]
    print("待处理 %d 页\n" % len(pages))

    ocr = RapidOCR()
    out = []
    docs = {}
    t0 = time.time()
    for i, (d, p) in enumerate(pages, 1):
        if d not in docs:
            docs[d] = pymupdf.open(os.path.join(PDF_ROOT, PDF_OF[d]))
        boxes = ocr_page(docs[d], p - 1, ocr)
        if only_one:
            print("=== %s p%d 共 %d 个框 ===" % (d[-6:], p, len(boxes)))
            print("%6s %6s %6s %6s  %s" % ("x0", "x1", "y0", "cy", "文字"))
            for b in sorted(boxes, key=lambda z: z["y0"]):
                print("%6.0f %6.0f %6.0f %6.0f  %s" % (b["x0"], b["x1"], b["y0"], b["cy"], b["t"][:40]))
        tbls = build_tables(boxes)
        if tbls:
            for t in tbls:
                t.update({"pdf": d, "page": p, "n_rows": len(t["rows"])})
                out.append(t)
            print("[%3d/%d] %s p%d  ✓ %d 张表  %s"
                  % (i, len(pages), d[-6:], p, len(tbls),
                     " | ".join("%s(%d行)" % (t["key_col"], len(t["rows"])) for t in tbls)))
        else:
            print("[%3d/%d] %s p%d  ✗ 没认出表格" % (i, len(pages), d[-6:], p))
        if only_one:
            print(json.dumps(tbls, ensure_ascii=False, indent=1)[:4000])

    for doc in docs.values():
        doc.close()

    dst = os.path.join(OCR_ROOT, "_药对表.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n认出 %d / %d 页表格，用时 %.1f 分钟" % (len(out), len(pages), (time.time() - t0) / 60))
    print("存：", dst)


if __name__ == "__main__":
    main()
