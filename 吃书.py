# -*- coding: utf-8 -*-
"""
吃书 —— 一条命令把一本扫描书吃进知识库

用法：
    py 吃书.py "D:\\Download\\某书.pdf"           默认书名叫文件名
    py 吃书.py "D:\\Download\\某书.pdf" "医案集2"
    py 吃书.py "D:\\Download\\某书目录"  "医案集2"   目录里多个 PDF 按名排序
    py 吃书.py --从表格 "D:\\...\\某书"  "医案集2"   OCR 已完成，只从表格往后跑

流程（就是之前手工做的那一遍，串起来）：
    ① OCR 全部页   裁底部水印 + 剔右侧栏
    ② 抽医案       四诊 → 方名 → 药味剂量  入 医案/处方 表
    ③ 重建表格     药对表 + 单味药表
    ④ 抽体质       体质 ← 症状
    ⑤ 抽加减/方性/结构符合方
    ⑥ 更新受控词表
    ⑦ 出报告

每本书的中间产物放在  医案集_OCR\\书\\<书名>\\ ，数据库是共用的（带「书」字段）。
"""
import glob
import io
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from 归一规则 import (归一药, 归一标签, 归一合方, 药对名, 篇章)   # noqa: E402

根 = HERE
库 = os.path.join(HERE, "中医大脑医案库.db")

# ---------- 正则 ----------
RE_辨证 = re.compile(r"辨[证辩]论治")
RE_方名 = re.compile(r"[\[【]\s*方名\s*[\]】]\s*(.+)")
RE_药 = re.compile(r"([\u4e00-\u9fff]{2,6})\s*[:：]?\s*([\d.]+)\s*[gG]")
RE_舌脉 = re.compile(r"^[\u4e00-\u9fa5]{2,8}(脉|苔|舌)$|^(舌|脉)")
RE_篇章 = re.compile("(" + "|".join(篇章) + ")")
RE_体质起 = re.compile(r"体质分析")
RE_体质止 = re.compile(r"中医大脑处方|中医大脑主方|处方中的用药分析")
RE_加减 = re.compile(r"[\[【］]\s*(主症加强|推荐加减)\s*[\]】］]\s*([^\n]*)")
RE_结构 = re.compile(r"结构符合的方剂(.{0,300})", re.S)
RE_方性 = re.compile(r"方性分析\s*\n\s*([^\n]{6,160})")
体质后缀 = "不足|不振|不交|不固|两虚|亏虚|虚弱|阻滞|虚|实|盛|固|郁|瘀|滞|亢|病|证|弱|亏"
RE_体名内联 = re.compile(r"^([\u4e00-\u9fff]{2,8}?(?:" + 体质后缀 + r"))")
RE_纯中文行 = re.compile(r"^[\u4e00-\u9fff]{2,12}$")
RE_有句读 = re.compile(r"[，、,]|舌|脉|苔")


# ================= ① OCR =================
def 找PDF(源):
    if os.path.isdir(源):
        fs = []
        for ext in ("*.pdf", "*.PDF"):
            fs += glob.glob(os.path.join(源, "**", ext), recursive=True)
        return sorted(fs)
    return [源]


def 跑OCR(pdfs, 出目录, dpi=144, 底部=0.92, 侧栏=0.86):
    """页码**跨 PDF 连续编号**，同时写一份 页码映射.json（全局页 -> 哪个PDF的第几页）"""
    import numpy as np
    import pymupdf
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
    os.makedirs(出目录, exist_ok=True)
    ocr = RapidOCR()
    映射 = {}
    g = 0
    for pi, pdf in enumerate(pdfs):
        doc = pymupdf.open(pdf)
        n = doc.page_count
        print("  OCR %s（%d 页，从第 %d 页开始编）" % (os.path.basename(pdf), n, g + 1), flush=True)
        for i in range(n):
            g += 1
            映射[str(g)] = [os.path.basename(pdf), i + 1]
            出 = os.path.join(出目录, "p%04d.txt" % g)
            if os.path.exists(出):
                continue
            pix = doc[i].get_pixmap(dpi=dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            w, h = img.size
            img = img.crop((0, 0, w, int(h * 底部)))
            w2 = img.size[0]
            arr = np.array(img)
            del pix
            img.close()
            res, _ = ocr(arr)
            del arr
            keep = []
            if res:
                for box, txt, sc in res:
                    x0 = min(p[0] for p in box)
                    if x0 > 侧栏 * w2:
                        continue
                    keep.append((min(p[1] for p in box), x0, txt))
            keep.sort(key=lambda r: (round(r[0] / 25), r[1]))
            open(出, "w", encoding="utf-8").write("\n".join(t for _, _, t in keep))
        doc.close()
    json.dump(映射, open(os.path.join(出目录, "_页码映射.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return g


# ================= ② 抽医案 =================
def 抽医案(ocr目录, 书名):
    rows = []
    cur = ""
    for fp in sorted(glob.glob(os.path.join(ocr目录, "*.txt"))):
        page = int(re.search(r"p(\d+)\.txt", fp).group(1))
        text = open(fp, encoding="utf-8").read()
        mp = RE_篇章.search(text[:200])
        if mp:
            cur = mp.group(1)
        m = RE_辨证.search(text)
        if not m:
            continue
        tail = text[m.end():]
        mf = RE_方名.search(tail)
        if not mf:
            continue
        原方 = re.sub(r"[\[\]【】]", "", mf.group(1)).strip()
        方 = 归一合方(原方) or "（大数据组方）"

        lines = [l.strip() for l in tail[:mf.start()].split("\n") if l.strip()]
        lines = [l for l in lines if "中医大脑" not in l and "问止" not in l]
        if len(lines) < 3:
            continue
        舌脉 = [l for l in lines if RE_舌脉.match(l)]
        症状 = [l for l in lines if l not in 舌脉 and 2 <= len(l) <= 12
                and not re.search(r"[。，、；：！？]", l)]

        药 = []
        for l in tail[mf.end():].split("\n")[:40]:
            l = l.strip()
            if 药 and len(药) >= 25:
                break
            if not re.search(r"[\d.]+\s*[gG]", l):
                if 药 and len(药) >= 3:
                    break
                continue
            for mm in RE_药.finditer(l):
                药.append((归一药(mm.group(1).strip()), float(mm.group(2))))
        rows.append({"书": 书名, "篇": cur, "页": page, "症状": 症状, "舌脉": 舌脉,
                     "方名": 方, "原方名": 原方, "药": 药})
    return rows


def 入医案(rows, 书名):
    con = sqlite3.connect(库)
    c = con.cursor()
    c.execute("DELETE FROM 医案 WHERE 书=?", (书名,))          # 重跑先清这本
    for r in rows:
        c.execute("INSERT INTO 医案(书,篇,页,症状,舌脉,方名,原方名) VALUES(?,?,?,?,?,?,?)",
                  (书名, r["篇"], r["页"],
                   json.dumps([x for x in (归一标签(s) for s in r["症状"]) if x], ensure_ascii=False),
                   json.dumps([x for x in (归一标签(s) for s in r["舌脉"]) if x], ensure_ascii=False),
                   r["方名"], r["原方名"]))
        cid = c.lastrowid
        for 名, 量 in r["药"]:
            c.execute("INSERT INTO 处方(医案id,药,剂量) VALUES(?,?,?)", (cid, 名, 量))
    con.commit()
    con.close()
    return len(rows)


# ================= ③ 表格 =================
def 抽表格(ocr目录, pdfs, 出json):
    """复用 表格重建.py 的算法；页码用全局编号，靠 页码映射.json 找回原 PDF"""
    import pymupdf
    from rapidocr_onnxruntime import RapidOCR
    import 表格重建 as T

    映射 = json.load(open(os.path.join(ocr目录, "_页码映射.json"), encoding="utf-8"))
    页 = []
    for fp in sorted(glob.glob(os.path.join(ocr目录, "p*.txt"))):
        t = open(fp, encoding="utf-8").read()
        if "药对分析" in t or "单味药分析" in t:
            页.append(int(re.search(r"p(\d+)\.txt", fp).group(1)))
    print("  待重建表格 %d 页" % len(页), flush=True)

    ocr = RapidOCR()
    docs, out = {}, []
    for i, g in enumerate(页, 1):
        pdf名, 本地页 = 映射[str(g)]
        pdf路径 = next((p for p in pdfs if os.path.basename(p) == pdf名), None)
        if not pdf路径:
            continue
        if pdf路径 not in docs:
            docs[pdf路径] = pymupdf.open(pdf路径)
        boxes = T.ocr_page(docs[pdf路径], 本地页 - 1, ocr)
        for t in T.build_tables(boxes):
            t.update({"pdf": pdf名, "page": g, "本地页": 本地页})
            out.append(t)
        if i % 10 == 0:
            print("     %d/%d" % (i, len(页)), flush=True)
    for d in docs.values():
        d.close()
    json.dump(out, open(出json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(out)


# ================= ④ 体质 =================
def 抽体质(ocr目录, 出json):
    def 解析(块):
        行 = [l.strip() for l in 块.split("\n") if l.strip()]
        行 = [l for l in 行 if not re.search(
            r"中医大脑|学习模块|体质倾向|体质特性|体质特点|体质性|参考依据|代表着他|我们把|细看其|不禁令人", l)]
        出, 当前 = [], None
        for l in 行:
            if RE_纯中文行.match(l) and not RE_有句读.search(l) and len(l) <= 8:
                当前 = {"名": l, "症": []}
                出.append(当前)
                continue
            m = RE_体名内联.match(l)
            if m and len(m.group(1)) <= 6:
                当前 = {"名": m.group(1), "症": [l[m.end():].strip()] if l[m.end():].strip() else []}
                出.append(当前)
                continue
            if 当前 is not None:
                当前["症"].append(l)
        净 = []
        for g in 出:
            词 = []
            for s in g["症"]:
                for x in re.split(r"[，,、]", s):
                    x = x.strip("。 .")
                    if 2 <= len(x) <= 14 and not re.match(r"^\d+$", x):
                        词.append(x)
            if 词:
                净.append({"体质": g["名"], "症状": 词, "出处": ""})
        return 净

    全部 = []
    for fp in sorted(glob.glob(os.path.join(ocr目录, "*.txt"))):
        text = open(fp, encoding="utf-8").read()
        m = RE_体质起.search(text)
        if not m:
            continue
        tail = text[m.end():]
        e = RE_体质止.search(tail)
        块 = tail[:e.start()] if e else tail[:2000]
        for g in 解析(块):
            g["出处"] = "p" + re.search(r"p(\d+)\.txt", fp).group(1)
            全部.append(g)
    json.dump(全部, open(出json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 全部


# ================= ⑤ 剩余块 =================
def 抽剩余(ocr目录, 出json, 方名池):
    jia, jie, xing = [], [], []
    for fp in sorted(glob.glob(os.path.join(ocr目录, "*.txt"))):
        text = open(fp, encoding="utf-8").read()
        pg = "p" + re.search(r"p(\d+)\.txt", fp).group(1)
        for m in RE_加减.finditer(text):
            药 = [{"药": 归一药(x.group(1)), "量": float(x.group(2))}
                  for x in RE_药.finditer(m.group(2))]
            if 药:
                jia.append({"类型": m.group(1), "药": 药, "原文": m.group(2).strip()[:90], "出处": pg})
        for m in RE_结构.finditer(text):
            句 = m.group(1).strip()
            命 = [n for n in 方名池 if n in 句]
            if 命:
                jie.append({"方": "、".join(sorted(set(命), key=len, reverse=True)[:6]),
                            "原文": 句[:150], "出处": pg})
        m = RE_方性.search(text)
        if m:
            描述 = re.sub(r"中医大脑可以就方剂的单味药药性和比例算出方性[，,、]?\s*"
                          r"并且列出以下的方性图[。.]?", "", m.group(1)).strip()
            描述 = re.sub(r"^[，,、。\s]+", "", 描述)
            描述 = re.sub(r"^中医大脑[^。]*。\s*", "", 描述).strip()
            if len(描述) >= 10 and "方性图" not in 描述[:20]:
                xing.append({"描述": 描述, "出处": pg})
    json.dump({"加减": jia, "结构符合方": jie, "方性": xing},
              open(出json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return jia, jie, xing


# ================= ⑥ 词表 =================
def 抽词(ocr目录, 出json):
    c = Counter()
    for fp in sorted(glob.glob(os.path.join(ocr目录, "*.txt"))):
        text = open(fp, encoding="utf-8").read()
        m = RE_辨证.search(text)
        if not m:
            continue
        tail = text[m.end():]
        cut = len(tail)
        for pat in [r"[\[【]\s*方名", r"[\[【]\s*自诉", r"中医大脑："]:
            mm = re.search(pat, tail)
            if mm:
                cut = min(cut, mm.start())
        for line in tail[:cut].split("\n"):
            s = line.strip().strip("，。、；：·．- ")
            if 2 <= len(s) <= 12 and not re.search(r"\d", s):
                n = 归一标签(s)
                if n:
                    c[n] += 1
    json.dump(dict(c), open(出json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return c


# ================= 主流程 =================
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    从表格 = "--从表格" in sys.argv
    if not args:
        print(__doc__)
        return
    源 = args[0]
    书名 = args[1] if len(args) > 1 else os.path.splitext(os.path.basename(源))[0]
    书目录 = os.path.join(根, "书", 书名)
    ocr目录 = os.path.join(书目录, "OCR")
    os.makedirs(ocr目录, exist_ok=True)
    pdfs = 找PDF(源)
    t0 = time.time()

    print("=" * 60)
    print("  吃书：%s   （%d 个 PDF）" % (书名, len(pdfs)))
    print("=" * 60)

    if not 从表格:
        print("\n① OCR")
        跑OCR(pdfs, ocr目录)

    print("\n② 抽医案")
    rows = 抽医案(ocr目录, 书名)
    n = 入医案(rows, 书名)
    print("   入库 %d 人次" % n)

    print("\n③ 重建表格")
    页数 = 抽表格(ocr目录, pdfs, os.path.join(书目录, "_表格.json"))
    print("   认出 %d 张表" % 页数)

    print("\n④ 抽体质")
    体 = 抽体质(ocr目录, os.path.join(书目录, "_体质.json"))
    print("   %d 条「体质←症状」" % len(体))

    print("\n⑤ 抽加减/方性/结构符合方")
    方名池 = set()
    for r in rows:
        for p in re.split(r"合|加", r["方名"]):
            if 2 <= len(p) <= 8:
                方名池.add(p)
    jia, jie, xing = 抽剩余(ocr目录, os.path.join(书目录, "_剩余块.json"), 方名池)
    print("   加减 %d / 结构符合方 %d / 方性 %d" % (len(jia), len(jie), len(xing)))

    print("\n⑥ 抽词表")
    词 = 抽词(ocr目录, os.path.join(书目录, "_词表.json"))
    print("   %d 个标签（去重）" % len(词))

    # 报告
    报 = os.path.join(书目录, "报告.txt")
    with open(报, "w", encoding="utf-8") as f:
        f.write("《%s》吃书报告  %s\n" % (书名, time.strftime("%Y-%m-%d %H:%M")))
        f.write("  医案入库   %d 人次\n" % n)
        f.write("  表格       %d 张\n" % 页数)
        f.write("  体质       %d 条\n" % len(体))
        f.write("  加减       %d 条\n" % len(jia))
        f.write("  结构符合方 %d 条\n" % len(jie))
        f.write("  方性       %d 条\n" % len(xing))
        f.write("  标签       %d 个\n" % len(词))
    print("\n" + "=" * 60)
    print("跑完，用时 %.1f 分钟。报告：%s" % ((time.time() - t0) / 60, 报))
    print("⚠️ 别忘了跑 _药对入库.py（本脚本只管抽，入库是它的事）")


if __name__ == "__main__":
    main()
