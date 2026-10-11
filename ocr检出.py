# -*- coding: utf-8 -*-
"""
《AI岐黄·中医大脑医案集》扫描件 -> OCR 文本

v2 修订（2026-10-11）：修内存问题
  1) dpi 200 -> 144  源图原生就是 143dpi，200 是在插值放大，白吃一倍内存
  2) 每页 gc.collect()，显式放掉张量
  3) 支持每批只跑 N 页后退出 —— 外层循环反复调用，每批一个独立进程

用法：
    py _run.py 40     只跑 40 页就退出（外层循环反复调）
    py _run.py        不限页数

输出：
    D:\\Download\\医案集_OCR\\<pdf名>\\p001.txt ...
"""
import gc
import io
import os
import sys
import time

import numpy as np
import pymupdf
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PDFS = [
    r"D:\Download\图片转pdf_20261011_144227.pdf",
    r"D:\Download\图片转pdf_20261011_144659.pdf",
    r"D:\Download\图片转pdf_20261011_145759.pdf",
    r"D:\Download\图片转pdf_20261011_150118.pdf",
    r"D:\Download\图片转pdf_20261011_150457.pdf",
    r"D:\Download\图片转pdf_20261011_150728.pdf",
]
OUT_ROOT = r"D:\Download\医案集_OCR"
BOT = 0.92        # 保留上面 92%（去底部手机水印）
SIDE = 0.86       # x0 超过 86% 宽的框 = 右侧栏，剔除
DPI = 144         # 源图原生约 143dpi，别调高（调高只是插值，白吃内存）
WEAK = 50         # 少于这么多字 = 可疑页


def main():
    maxn = int(sys.argv[1]) if len(sys.argv) > 1 else 10 ** 9
    os.makedirs(OUT_ROOT, exist_ok=True)

    ocr = RapidOCR()
    done = weak = 0
    processed = ""

    for pdf in PDFS:
        if done >= maxn:
            break
        if not os.path.exists(pdf):
            print("!! 找不到：" + pdf, flush=True)
            continue
        stem = os.path.splitext(os.path.basename(pdf))[0]
        outdir = os.path.join(OUT_ROOT, stem)
        os.makedirs(outdir, exist_ok=True)

        doc = pymupdf.open(pdf)
        n = doc.page_count
        for i in range(n):
            if done >= maxn:
                break
            out = os.path.join(outdir, "p%03d.txt" % (i + 1))
            if os.path.exists(out):
                continue

            t0 = time.time()
            pix = doc[i].get_pixmap(dpi=DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            w, h = img.size
            img = img.crop((0, 0, w, int(h * BOT)))
            w2 = img.size[0]
            arr = np.array(img)

            # 用完就放，别等着 GC 自己收
            del pix
            img.close()

            res, _ = ocr(arr)
            del arr

            keep = []
            if res:
                for box, txt, score in res:
                    x0 = min(p[0] for p in box)
                    if x0 > SIDE * w2:
                        continue                      # 右侧栏
                    y0 = min(p[1] for p in box)
                    keep.append((y0, x0, txt))
            keep.sort(key=lambda r: (round(r[0] / 25), r[1]))
            text = "\n".join(t for _, _, t in keep)

            with open(out, "w", encoding="utf-8") as f:
                f.write(text)

            done += 1
            if len(text) < WEAK:
                weak += 1
            print("[%s] p%03d/%d  %4d字  %2.0fs%s"
                  % (stem[-6:], i + 1, n, len(text), time.time() - t0,
                     "  <<< 弱" if len(text) < WEAK else ""), flush=True)

            gc.collect()
        doc.close()
        processed = stem

    print("BATCHDONE 本次认了 %d 页，其中弱页 %d 张" % (done, weak), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。重跑会接着来。", flush=True)
    except Exception:
        import traceback
        traceback.print_exc()
