# -*- coding: utf-8 -*-
"""
中医大脑面板 —— 本地自用
输入四诊 → 输出候选方 + 依据 + 剂量参考 + 相似病案

跑法：py "D:\Download\医案集_OCR\中医大脑面板.py"
     浏览器开 http://127.0.0.1:8020
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 检索 import 中医库, 展开, DB          # noqa: E402
from 模型判断 import (全判, 抽取_KEY, 抽取_URL, 抽取_MODEL,
                     推演_KEY, 推演_URL, 推演_MODEL, 推演_TEMP)   # noqa: E402
from 问诊框架 import 框架, 造用户消息             # noqa: E402

端口 = 8020
app = FastAPI()
库 = 中医库()

# 受控词表
_词 = json.load(open(os.path.join(os.path.dirname(DB), "_受控词表.json"), encoding="utf-8"))
词表 = {"舌脉": _词["舌脉"], "症状": _词["症状"]}


class 请求(BaseModel):
    标签: list[str]


class 判断请求(BaseModel):
    标签: list[str]
    候选: list[str]
    原文: str = ""


class 问诊请求(BaseModel):
    输入: str
    标签: list[str] = []


class 录入请求(BaseModel):
    患者: str = ""
    四诊原文: str = ""
    证型: str = ""
    方名: str = ""
    处方: str = ""
    效果: str = ""
    备注: str = ""
    随访天数: int = 14


class 随访请求(BaseModel):
    id: int
    效果: str = ""
    随访日期: str = ""


RE_YAO_TXT = re.compile(r"([一-鿿]{2,6})\s*([\d.]+)\s*[gG]")


def 连库():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    # 旧库可能没有这两列，只加不删
    for col, typ in [("随访日期", "TEXT"), ("随访完成", "INTEGER")]:
        try:
            c.execute("ALTER TABLE 我的病案 ADD COLUMN %s %s" % (col, typ))
        except Exception:
            pass
    return c


@app.post("/录入")
def 录入接口(请求数据: 录入请求):
    """存一次自己的接诊 —— 这是攒自己数据的入口"""
    try:
        标签 = 抽标签(请求数据.四诊原文, [])
        症状 = [t for t in 标签 if not any(k in t for k in "舌脉苔")]
        舌脉 = [t for t in 标签 if any(k in t for k in "舌脉苔")]
        药 = [{"药": m.group(1), "量": float(m.group(2))} for m in RE_YAO_TXT.finditer(请求数据.处方)]
        c = 连库()
        随访 = (datetime.now() + timedelta(days=请求数据.随访天数)).strftime("%Y-%m-%d") if 请求数据.随访天数 else None
        c.execute("""INSERT INTO 我的病案
            (日期,患者,四诊原文,症状,舌脉,证型,方名,处方,效果,备注,随访日期,随访完成)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""",
                  (datetime.now().strftime("%Y-%m-%d"), 请求数据.患者, 请求数据.四诊原文,
                   json.dumps(症状, ensure_ascii=False), json.dumps(舌脉, ensure_ascii=False),
                   请求数据.证型, 请求数据.方名,
                   json.dumps(药, ensure_ascii=False), 请求数据.效果, 请求数据.备注, 随访))
        c.commit()
        c.close()
        return {"ok": True, "抽到标签": 标签, "抽到药": ["%s%gg" % (x["药"], x["量"]) for x in 药],
                "随访日期": 随访}
    except Exception:
        import traceback
        return {"错误": traceback.format_exc()}


@app.get("/我的病案")
def 列我的病案():
    c = 连库()
    rows = [dict(r) for r in c.execute("SELECT * FROM 我的病案 ORDER BY id DESC LIMIT 100")]
    n = c.execute("SELECT COUNT(*) FROM 我的病案").fetchone()[0]
    c.close()
    return {"共": n, "列表": rows}


@app.get("/待回访")
def 待回访():
    """到期 / 过期还没填效果的病案 —— 这是 Y 的来源，攒数据的那个入口"""
    c = 连库()
    rows = [dict(r) for r in c.execute("""
        SELECT id,日期,患者,证型,方名,处方,随访日期 FROM 我的病案
        WHERE (随访完成 IS NULL OR 随访完成=0) AND 随访日期 IS NOT NULL AND 随访日期<='今'
        ORDER BY 随访日期""".replace("'今'", "date('now')"))]
    c.close()
    return {"待回访": rows}


@app.post("/随访")
def 随访接口(请求数据: 随访请求):
    """回访到了就填效果 —— 这一步填下去，「这个方对不对」才从猜变成有数"""
    c = 连库()
    c.execute("UPDATE 我的病案 SET 效果=?, 随访日期=?, 随访完成=1 WHERE id=?",
              (请求数据.效果, 请求数据.随访日期 or datetime.now().strftime("%Y-%m-%d"), 请求数据.id))
    c.commit()
    c.close()
    return {"ok": True}


@app.post("/评测")
def 跑评测(条数: int = 30):
    """跑评测：拿库里的病案，让模型四选一，看准确率"""
    try:
        import 评测 as P
        return P.跑(条数)
    except Exception:
        import traceback
        return {"错误": traceback.format_exc()}


def 模型抽标签(文本):
    """让模型从受控词表里挑标签 —— 硬匹配认不出的（"大便偏溏"↔"大便溏"）它能对上"""
    词 = 词表["舌脉"] + 词表["症状"]
    p = ("下面是一位中医师对病人的四诊描述。请从【可选标签】里挑出这段话**确实包含**的标签。\n"
         "规则：只从给定标签里挑，不要自己造词；没提到的绝对不要挑；"
         "同一个意思换种说法也要挑出来（例：\"大便偏溏\"→\"大便溏\"，\"苔白\"→\"舌苔白\"，"
         "\"脉沉细\"→\"沉脉\"+\"细脉\"）。\n"
         '只输出 JSON：{"标签": ["…","…"]}，不要任何别的文字。\n\n'
         "【可选标签】\n" + "、".join(词) + "\n\n"
         "【四诊描述】\n" + 文本)
    try:
        r = requests.post(
            抽取_URL,
            headers={"Authorization": "Bearer " + 抽取_KEY, "Content-Type": "application/json"},
            json={"model": 抽取_MODEL, "messages": [{"role": "user", "content": p}],
                  "temperature": 0, "response_format": {"type": "json_object"}},
            timeout=90)
        r.raise_for_status()
        出 = json.loads(r.json()["choices"][0]["message"]["content"]).get("标签", [])
        合法 = set(词)
        return [t for t in 出 if t in 合法]
    except Exception as e:
        print("[模型抽标签失败]", e)
        return []


def 抽标签(文本, 已有):
    """硬匹配 + 简称表 + 模型兜底"""
    if 已有:
        return sorted(set(已有))
    hit = [t for t in 词表["舌脉"] + 词表["症状"] if t in 文本]
    for k, vs in {"苔白腻": ["舌苔白"], "苔黄腻": ["舌苔黄"], "齿痕": ["舌有齿痕"],
                  "便溏": ["大便溏", "软便"], "便秘": ["便秘", "大便干"],
                  "失眠": ["不易入睡", "半夜会醒过来"]}.items():
        if k in 文本:
            hit.extend(vs)
    return sorted(set(hit) | set(模型抽标签(文本)))


def 造材料(标签):
    """把库检索结果排成给模型读的文本"""
    症状 = [t for t in 标签 if not any(k in t for k in "舌脉苔")]
    舌脉 = [t for t in 标签 if any(k in t for k in "舌脉苔")]
    命中 = 库.匹配药对(标签, topn=12)
    候选 = 库.候选方(命中)[:5]
    相似 = 库.相似病案(症状, 舌脉, topn=8)
    if not 标签:
        return "（本次未能在库中匹配到标签，请仅依据医师输入推演，并说明信息不足）"

    out = ["### 命中标签\n" + "、".join(标签)]

    体 = 库.体质反查(标签)
    if 体:
        out.append("\n### 体质倾向（这本书里，这类症状组合对应到哪些体质 —— 多观点并列）")
        for x in 体:
            out.append("- **%s** ｜ 书里 %d 条病案命中 ｜ 共有症状：%s"
                       % (x["体质"], x["命中条数"], "、".join(x["共有"][:9])))
    else:
        out.append("\n### 体质倾向\n（本次症状组合在库里的体质表中没有命中的条目）")

    out.append("\n### 命中药对（药对 → 功效 → 适应证原文）")
    if 命中:
        for r in 命中:
            out.append("- **%s** ｜ %s ｜ （本次命中：%s）\n    %s"
                       % (r["药对"], r["主治"] or "", "、".join(r["命中"]),
                          (r["应用"] or "")[:180]))
    else:
        out.append("（无）")

    out.append("\n### 从命中药对反推出的候选方")
    if 候选:
        for f, w, pairs in 候选:
            out.append("- %s（权重 %d，依据：%s）" % (f, w, "、".join(pairs[:4])))
    else:
        out.append("（无）")

    out.append("\n### 剂量分布（该库实际用量，非药典）")
    药s = set()
    for f, _, _ in 候选[:2]:
        for m in 库.医案:
            if m["方名"] == f:
                for x in 库.con.execute("SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],)):
                    药s.add(x["药"])
                break
    for y in sorted(药s):
        d = 库.剂量.get(y)
        if d:
            out.append("- %s：%g~%g g（中位 %g，库内 %d 次）"
                       % (y, d["最小"], d["最大"], d["中位"], d["出现次数"]))

    out.append("\n### 相似病案（历史上最接近的，附该书当时的加减/方性/结构分析）")
    if 相似:
        for s in 相似:
            out.append("- %s ｜ %s %s ｜ 共有：%s"
                       % (s["方名"], s["篇"], s["页"], "、".join(s["共有"][:10])))
            f = s.get("附属") or {}
            if f.get("加减"):
                out.append("    加减：%s" % "；".join(f["加减"]))
            if f.get("结构"):
                out.append("    结构符合方剂：%s" % "；".join(f["结构"]))
            if f.get("方性"):
                out.append("    方性：%s" % "；".join(f["方性"]))
    else:
        out.append("（无）")
    return "\n".join(out)


@app.get("/标签")
def 取标签():
    return 词表


@app.post("/查")
def 查(请求数据: 请求):
  try:
    标签 = []
    for t in 请求数据.标签:
        标签.extend(展开(t))
    标签 = sorted(set(标签))
    症状 = [t for t in 标签 if not any(k in t for k in "舌脉苔")]
    舌脉 = [t for t in 标签 if any(k in t for k in "舌脉苔")]

    命中 = 库.匹配药对(标签, topn=10)
    候选 = 库.候选方(命中)[:5]
    相似 = 库.相似病案(症状, 舌脉, topn=8)

    # 候选方涉及的药 + 剂量
    剂量 = []
    if 候选:
        for m in 库.医案:
            if m["方名"] == 候选[0][0]:
                for x in 库.con.execute("SELECT 药 FROM 处方 WHERE 医案id=?", (m["id"],)):
                    d = 库.剂量.get(x["药"])
                    if d and x["药"] not in [z["药"] for z in 剂量]:
                        剂量.append({"药": x["药"], "最小": d["最小"], "中位": d["中位"],
                                     "最大": d["最大"], "次数": d["出现次数"]})
                break

    return {
        "标签": 标签,
        "命中药对": [{"药对": r["药对"], "主治": r["主治"], "应用": r["应用"],
                      "命中": r["命中"], "n": r["n"]} for r in 命中],
        "候选方": [{"方名": f, "权重": w, "依据": p} for f, w, p in 候选],
        "剂量": sorted(剂量, key=lambda x: -x["次数"]),
        "相似病案": 相似,
    }
  except Exception:
    import traceback
    return {"错误": traceback.format_exc()}


@app.post("/判断")
def 判断接口(请求数据: 判断请求):
    """把库检索出的候选方交给两个模型独立再判一遍"""
    try:
        t = []
        for x in 请求数据.标签:
            t.extend(展开(x))
        t = sorted(set(t))
        症状 = [x for x in t if not any(k in x for k in "舌脉苔")]
        舌脉 = [x for x in t if any(k in x for k in "舌脉苔")]
        return 全判(请求数据.候选, 症状, 舌脉, 请求数据.原文)
    except Exception:
        import traceback
        return {"错误": traceback.format_exc()}


@app.post("/问诊")
def 问诊接口(请求数据: 问诊请求):
    """AI 问诊：检索喂料 + 思维框架 + 大模型推演（流式）"""
    标签 = 抽标签(请求数据.输入, 请求数据.标签)
    材料 = 造材料(标签)
    消息 = 造用户消息(请求数据.输入, 材料)

    def 生成():
        yield "【本次库内命中的标签】\n" + ("、".join(标签) if 标签 else "（一个都没命中）") + "\n\n"
        yield "─" * 30 + "\n\n"
        try:
            r = requests.post(
                推演_URL,
                headers={"Authorization": "Bearer " + 推演_KEY, "Content-Type": "application/json"},
                json={"model": 推演_MODEL,
                      "messages": [{"role": "system", "content": 框架},
                                   {"role": "user", "content": 消息}],
                      "temperature": 推演_TEMP, "stream": True},
                stream=True, timeout=300)
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                s = line.decode("utf-8", "ignore")
                if not s.startswith("data:"):
                    continue
                payload = s[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    d = json.loads(payload)
                    delta = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
                except Exception:
                    continue
        except Exception as e:
            yield "\n\n[出错] " + str(e)[:300]

    return StreamingResponse(生成(), media_type="text/plain; charset=utf-8")


class 抽标签请求(BaseModel):
    输入: str


@app.post("/抽标签")
def 抽标签接口(请求数据: 抽标签请求):
    """自由文本 → 受控标签。给前端拿去预填表单，让人核对。"""
    try:
        t = 抽标签(请求数据.输入, [])
        return {"标签": t, "全部": 词表}
    except Exception:
        import traceback
        return {"错误": traceback.format_exc()}


@app.get("/", response_class=HTMLResponse)
def 首页():
    return HTML


HTML = r"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>中医大脑面板</title>
<style>
:root{--bg:#f7f5f2;--card:#fff;--line:#e5e0d8;--ink:#22201d;--dim:#8a8580;--accent:#8c2f21;--hi:#f3ece4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;-webkit-text-size-adjust:100%}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:19px;margin:0 0 2px;letter-spacing:1px}
.sub{color:var(--dim);font-size:12.5px;margin-bottom:18px}
.cols{display:grid;grid-template-columns:minmax(280px,380px) 1fr;gap:16px}
@media(max-width:820px){.cols{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card h2{font-size:13px;margin:0 0 10px;color:var(--dim);font-weight:600;letter-spacing:.5px}
.grp{margin-bottom:14px}
.grp>b{display:block;font-size:12px;color:var(--dim);margin-bottom:6px}
.tags{display:flex;flex-wrap:wrap;gap:6px}
.tags label{border:1px solid var(--line);border-radius:20px;padding:3px 10px;font-size:12.5px;
 cursor:pointer;user-select:none;background:#fff}
.tags input{display:none}
.tags input:checked+span{color:#fff}
.tags label:has(input:checked){background:var(--ink);border-color:var(--ink);color:#fff}
#搜索{width:100%;padding:8px 11px;border:1px solid var(--line);border-radius:8px;font:inherit;margin-bottom:12px}
button{width:100%;padding:13px;background:var(--ink);color:#fff;border:0;border-radius:8px;
 font:600 14px/1 inherit;cursor:pointer;letter-spacing:2px;margin-top:6px}
button:hover{background:#3a3733}
button:disabled{background:#c3beb7;cursor:default}
button.mini{margin-top:0;width:auto;padding:9px 16px;font-size:13px;letter-spacing:0;background:var(--accent)}
button.mini:hover{background:#a33b2b}
.一致{color:#2e6b3f;font-weight:600}
.分歧{color:var(--accent);font-weight:600}
.row{border-bottom:1px solid var(--line);padding:9px 0}
.row:last-child{border:0}
.row .k{font-weight:600}
.row .v{color:var(--dim);font-size:12.5px}
.hit{display:inline-block;background:var(--hi);border-radius:4px;padding:0 5px;margin:0 3px 2px 0;font-size:12px}
.w{float:right;font-size:12px;color:var(--accent);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td,th{padding:5px 4px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--dim);font-weight:500}
.empty{color:var(--dim);font-size:13px}
mark{background:var(--hi);color:var(--ink);border-radius:3px;padding:0 2px}
.note{font-size:12px;color:var(--dim);background:var(--hi);border-radius:6px;
 padding:9px 11px;margin:0 0 12px;line-height:1.65}
.note b{color:var(--ink)}
.tabs{display:flex;gap:6px;margin-bottom:16px}
.tab{flex:1;padding:10px;border:1px solid var(--line);background:#fff;color:var(--dim);
 border-radius:8px;font:600 13px/1 inherit;cursor:pointer;letter-spacing:0;margin:0;width:auto}
.tab.on{background:var(--ink);border-color:var(--ink);color:#fff}
.tab:hover{background:#f0ece6}
.tab.on:hover{background:var(--ink)}
#四诊{width:100%;height:210px;padding:11px 12px;border:1px solid var(--line);border-radius:8px;
 font:13px/1.7 inherit;resize:vertical;outline:none}
#四诊:focus{border-color:var(--ink)}
.stream{white-space:pre-wrap;font:13.5px/1.85 inherit;margin:0;word-break:break-word}
</style></head><body><div class="wrap">
<h1>中医大脑面板</h1>
<div class="sub">输入四诊 → 候选方 + 依据 + 剂量参考 + 相似病案 ｜ 数据源：《AI岐黄·中医大脑医案集》 ｜ 自用</div>
<div class="cols">
  <div class="card">
    <div class="tabs">
      <button class="tab on" data-m="查">快查</button>
      <button class="tab" data-m="问诊">问诊（AI）</button>
      <button class="tab" data-m="病案">我的病案</button>
    </div>

    <div id="区查">
      <input id="搜索" placeholder="筛标签…（如 苔 / 便 / 脉）">
      <div class="grp" id="g舌脉"><b>舌象 / 脉象</b><div class="tags"></div></div>
      <div class="grp" id="g症状"><b>症状</b><div class="tags"></div></div>
      <button id="跑">查（快 · 纯检索）</button>
      <button id="跑问" class="mini" style="width:100%;padding:12px;margin-top:8px">带这些标签问 AI（慢 · 30~60 秒）</button>
      <div class="v" style="margin-top:6px;font-size:11.5px">上面是毫秒级的代码检索；下面的走完整推演，出会诊意见。</div>
    </div>

    <div id="区问诊" hidden>
      <textarea id="四诊" placeholder="把四诊写在这里，自由文字就行。例：&#10;&#10;男，42岁。怕冷，手脚冰凉，腹部怕凉，遇冷即腹泻，泻后舒服。大便一天2-3次偏溏。夜尿2-3次。腰膝酸冷。&#10;&#10;舌：舌体胖大、边有齿痕，舌质淡，舌苔白滑，中根部略厚&#10;脉：右寸关沉细弱，尺部尤弱；左关略弦"></textarea>
      <div class="v" style="margin:10px 0 12px;font-size:12px">不用勾标签——系统会自己从受控词表里抽。缺什么它会指出来。</div>
      <button id="抽" class="mini" style="width:100%;padding:12px">抽取标签 → 我来核对</button>
      <button id="问">问 诊</button>
    </div>

    <div id="区病案" hidden>
      <div class="v" style="font-size:12px;margin-bottom:8px">
        这一页是你的病案库 —— 书里全是成功案例，<b>「这个方到底对不对」只有你能答</b>。
        接诊存一条，<b>默认 14 天后回访</b>，到期这页会标出来。
      </div>
      <div class="v" style="font-size:12px;margin-bottom:4px">录入一条接诊</div>
      <input id="患者" placeholder="患者（不写也行）" style="margin-bottom:6px">
      <textarea id="病案四诊" placeholder="四诊原文（自由写）" style="height:80px"></textarea>
      <div style="display:flex;gap:6px;margin:6px 0">
        <input id="证型" placeholder="证型" style="flex:1">
        <input id="方名" placeholder="方名" style="flex:1">
      </div>
      <input id="处方" placeholder="处方，例：桂枝15g 白芍15g 炙甘草10g" style="margin-bottom:6px">
      <div style="display:flex;gap:6px;margin-bottom:8px">
        <input id="随访天数" type="number" value="14" style="width:80px">
        <span class="v" style="font-size:11px;margin-top:10px">天后回访</span>
        <button id="录病案" style="flex:1;margin:0">存入病案</button>
      </div>
      <div class="v" style="font-size:12px;margin:14px 0 4px">已存的病案（点「填回访」补效果）</div>
      <div id="病案列表"></div>
    </div>
  </div>
  <div id="结果"></div>
</div></div>
<script>
let 词表={舌脉:[],症状:[]};
const $=s=>document.querySelector(s);
const esc=s=>(s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

// 脚本一出错就显示出来，别静默失败
window.onerror=(m,src,ln,col)=>{const e=document.getElementById("结果");
  if(e)e.innerHTML='<div class="card" style="border-color:#e0b4ad"><b style="color:#8c2f21">页面脚本出错</b>'+
  '<pre class="stream" style="margin-top:8px;font-size:12px">'+esc(String(m))+'  @行 '+ln+'</pre></div>';};

function 渲染(list,box){
  box.innerHTML=list.map((t,i)=>`<label><input type="checkbox" value="${esc(t)}"><span>${esc(t)}</span></label>`).join("");
}
fetch("/标签").then(r=>r.json()).then(d=>{
  词表=d;
  渲染(d.舌脉,$("#g舌脉 .tags"));渲染(d.症状,$("#g症状 .tags"));
});

$("#搜索").oninput=e=>{
  const q=e.target.value.trim();
  const f=l=>q?l.filter(t=>t.includes(q)):l;
  渲染(f(词表.舌脉),$("#g舌脉 .tags"));渲染(f(词表.症状),$("#g症状 .tags"));
};

// 切换：快查 / 问诊 / 病案
document.querySelectorAll(".tab").forEach(b=>{
  b.onclick=()=>{
    document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on",x===b));
    const m=b.dataset.m;
    $("#区查").hidden = (m!=="查");
    $("#区问诊").hidden = (m!=="问诊");
    $("#区病案").hidden = (m!=="病案");
    if(m==="病案") 载病案();
    $("#结果").innerHTML = (m==="问诊")
      ? '<div class="card"><span class="empty">左边写好四诊，再按下面的「问 诊」。</span></div>' : "";
  };
});

// 我的病案 —— 列表 + 回访
async function 载病案(){
  const r=await fetch("/我的病案");
  const d=await r.json();
  const rows=d.列表||[];
  let h="";
  if(!rows.length){h='<div class="empty" style="padding:12px 0">还没有病案。存第一条。</div>';}
  rows.forEach(v=>{
    const 回访到期 = v.随访日期 && !v.随访完成 && v.随访日期 <= new Date().toISOString().slice(0,10);
    const 效果=v.效果||"";
    h+=`<div class="card" style="margin-bottom:10px;padding:12px 14px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <div><b>${esc(v.患者||"—")}</b> <span class="v">${esc(v.日期||"")}</span></div>
        <div class="v" style="font-size:11.5px">${esc(v.证型||"")}${v.方名?" · "+esc(v.方名):""}</div>
      </div>
      ${v.处方?`<div class="v" style="font-size:11.5px;margin-top:6px">${esc(v.处方)}</div>`:""}
      ${效果?`<div style="margin-top:8px;padding:8px 10px;background:#f6f3ee;border-radius:6px;font-size:12.5px"><b>效果：</b>${esc(效果)}</div>`
        : (回访到期
          ? `<div style="margin-top:8px;padding:8px 10px;background:#fdf3f0;border:1px solid #e0b4ad;border-radius:6px">
              <b style="color:#8c2f21">到回访时间了</b>（${esc(v.随访日期)}）
              <div style="display:flex;gap:6px;margin-top:8px">
                <input id="效${v.id}" placeholder="效果怎么样（好点/没变化/更差）" style="flex:1">
                <button class="mini" onclick="填回访(${v.id})" style="padding:6px 14px">填回访</button>
              </div></div>`
          : `<div class="v" style="margin-top:6px;font-size:11.5px">回访 ${esc(v.随访日期||"未定")}${v.随访完成?"（已填）":""}</div>`)}
    </div>`;
  });
  $("#病案列表").innerHTML=h;
}
window.填回访=async(id)=>{
  const v=$("#效"+id).value.trim();
  if(!v){alert("写点效果再存");return;}
  await fetch("/随访",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id:id,效果:v})});
  载病案();
};

// 录入一条病案
$("#录病案").onclick=async()=>{
  const d={患者:$("#患者").value.trim(), 四诊原文:$("#病案四诊").value.trim(),
    证型:$("#证型").value.trim(), 方名:$("#方名").value.trim(), 处方:$("#处方").value.trim(),
    随访天数:parseInt($("#随访天数").value)||0};
  const r=await fetch("/录入",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(d)});
  const j=await r.json();
  if(j.ok){
    $("#病案列表").insertAdjacentHTML("afterbegin",
      '<div class="card" style="margin-bottom:10px;padding:10px 12px;border-left:3px solid #8c2f21;font-size:12.5px">'+
      '已存。'+esc(j.抽到药.join("、")||"未抽到药")+'；回访 '+esc(j.随访日期||"未定")+'</div>');
    ["患者","病案四诊","证型","方名","处方"].forEach(id=>$("#"+id).value="");
    载病案();
  }else{
    alert("存失败了："+String(j.错误||"").slice(0,200));
  }
};

// 抽取标签 -> 切到快查、预勾选、让人核对
$("#抽").onclick=async()=>{
  const t=$("#四诊").value.trim();
  if(!t){$("#结果").innerHTML='<div class="card"><span class="empty">先写点四诊。</span></div>';return;}
  const b=$("#抽"); b.disabled=true; b.textContent="抽取中…";
  try{
    const r=await fetch("/抽标签",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({输入:t})});
    const d=await r.json();
    if(d.错误){
      $("#结果").innerHTML='<div class="card"><span class="empty">出错：'+esc(String(d.错误).slice(0,300))+'</span></div>';
    }else{
      // 切到「快查」，清掉搜索过滤，重渲染全部标签
      document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on",x.dataset.m==="查"));
      $("#区查").hidden=false; $("#区问诊").hidden=true;
      $("#搜索").value="";
      渲染(词表.舌脉,$("#g舌脉 .tags"));渲染(词表.症状,$("#g症状 .tags"));
      const 集=new Set(d.标签||[]);
      document.querySelectorAll('#区查 input[type=checkbox]').forEach(x=>{x.checked=集.has(x.value);});
      $("#结果").innerHTML='<div class="card"><h2>抽取结果 —— 请核对</h2>'+
        '<div class="note">抽出 <b>'+(d.标签||[]).length+'</b> 个标签，已在左边勾好。<br>'+
        '<b>对的不用动、多的取消、少的补勾</b>，然后按「查」。<br>'+
        '⚠️ 抽漏是常事（例："大便偏溏"这种换了个说法的可能认不出）—— <b>缺哪个自己补上</b>。</div>'+
        '<div>'+((d.标签||[]).map(x=>'<span class="hit">'+esc(x)+'</span>').join("")||'<span class="empty">一个都没抽到</span>')+'</div></div>';
    }
  }catch(e){$("#结果").innerHTML='<div class="card"><span class="empty">连不上：'+esc(e)+'</span></div>';}
  b.disabled=false; b.textContent="抽取标签 → 我来核对";
};

// 问诊（流式）—— 两个入口共用：
//   ① 问诊页签直接问（标签传空，后端自己抽）
//   ② 快查页签核对完标签后带着问（标签由人确定，后端不再抽）
async function 流式问诊(标签, 原文本, b, 原名){
  b.disabled=true; b.textContent="推演中…";
  $("#结果").innerHTML=
    '<div class="card"><div id="think" class="note" style="margin-bottom:14px">'+
    '正在推演…　<b>首次出字要等 20~40 秒</b>（模型先在内部思考，这段时间它不会输出任何字，不是卡住）。</div>'+
    '<pre class="stream" id="st"></pre></div>';
  const pre=$("#st"), think=$("#think");
  const t0=Date.now();
  let 已出字=false;
  const timer=setInterval(()=>{
    const s=Math.round((Date.now()-t0)/1000);
    think.innerHTML = 已出字
      ? '推演中…　已 '+s+' 秒'
      : '正在推演…　已 '+s+' 秒　<b>（首次出字要等 20~40 秒，不是卡住）</b>';
  },1000);
  try{
    const r=await fetch("/问诊",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({输入:原文本,标签:标签})});
    if(!r.ok){pre.textContent="服务返回 HTTP "+r.status+"\n"+(await r.text()).slice(0,500);}
    else{
      const rd=r.body.getReader(), dec=new TextDecoder("utf-8");
      while(true){
        const {done,value}=await rd.read();
        if(done)break;
        const s=dec.decode(value,{stream:true});
        pre.textContent+=s;
        if(!已出字 && pre.textContent.length>90){已出字=true;}
      }
    }
  }catch(e){pre.textContent+="\n\n[出错] "+e;}
  clearInterval(timer);
  think.innerHTML='完成，用时 '+Math.round((Date.now()-t0)/1000)+' 秒';
  b.disabled=false; b.textContent=原名;
}

// 入口①：问诊页签直接问
$("#问").onclick=()=>{
  const t=$("#四诊").value.trim();
  if(!t){$("#结果").innerHTML='<div class="card"><span class="empty">先写点四诊。</span></div>';return;}
  流式问诊([], t, $("#问"), "问 诊");
};

// 入口②：快查页签核对完标签，带着这些标签问（标签是人工确认过的，后端不再抽）
$("#跑问").onclick=()=>{
  const 标签=[...document.querySelectorAll('#区查 input:checked')].map(x=>x.value);
  if(!标签.length){$("#结果").innerHTML='<div class="card"><span class="empty">先在左边勾几个标签。</span></div>';return;}
  const t=$("#四诊").value.trim();
  流式问诊(标签, t || ("（由勾选的标签提交）"), $("#跑问"), "带这些标签问 AI（慢 · 30~60 秒）");
};

$("#跑").onclick=async()=>{
  const 标签=[...document.querySelectorAll('input:checked')].map(x=>x.value);
  if(!标签.length){$("#结果").innerHTML='<div class="card"><span class="empty">先选几个标签。</span></div>';return;}
  const b=$("#跑");b.disabled=true;b.textContent="查中…";
  try{
    const r=await fetch("/查",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({标签})});
    const d=await r.json();
    画(d);
  }catch(e){$("#结果").innerHTML='<div class="card"><span class="empty">出错：'+esc(e)+'</span></div>';}
  b.disabled=false;b.textContent="查";
};

function 画(d){
  let h="";
  h+=`<div class="card"><h2>候选方</h2>
    <div class="note"><b>权重</b> = 命中的药对里，有几个被这个方的处方包含
      （每个药对按其「命中标签数」计分，含得越多分越高）。<br>
      ⚠️ 这是<b>粗指标</b>：它奖励药味多的方（药多自然含的药对多），
      <b>"含这个药对"≠"对这个证"</b>。真正的依据请看下面的「相似病案」和「命中药对」。</div>`;
  if(!d.候选方.length) h+=`<span class="empty">没有反推出候选方（命中的药对没落在库里的方上）</span>`;
  d.候选方.forEach(c=>{
    h+=`<div class="row"><span class="w">权重 ${c.权重}</span><span class="k">${esc(c.方名)}</span>
        <div class="v">← ${c.依据.map(esc).join("、")}</div></div>`;
  });
  h+=`</div>`;

  if(d.候选方.length>=2){
    h+=`<div class="card" style="margin-top:16px"><h2>模型补充判断</h2>
      <div class="v" style="margin-bottom:10px">把上面的候选交给 Jev 和 DeepSeek 各独立判一遍。
      两个一致 = 高可信；分歧 = ⚠️ 需人工复核（27 条实测：一致 88% / 分歧 45%）。</div>
      <button class="mini" id="pbtn">让两个模型判一下</button>
      <div id="pout" style="margin-top:12px"></div></div>`;
  }

  h+=`<div class="card" style="margin-top:16px"><h2>命中药对（依据）</h2>`;
  if(!d.命中药对.length) h+=`<span class="empty">没命中</span>`;
  d.命中药对.forEach(r=>{
    h+=`<div class="row"><span class="k">${esc(r.药对)}</span>
        <span class="v"> — ${esc(r.主治||"")}</span>
        <div class="v">${r.命中.map(t=>'<span class="hit">'+esc(t)+'</span>').join("")}</div>
        <div class="v">${esc((r.应用||"").slice(0,140))}</div></div>`;
  });
  h+=`</div>`;

  if(d.剂量.length){
    h+=`<div class="card" style="margin-top:16px"><h2>剂量参考（库内分布，非药典）</h2>
     <table><tr><th>药</th><th>最小</th><th>中位</th><th>最大</th><th>库内次数</th></tr>`;
    d.剂量.forEach(x=>{h+=`<tr><td>${esc(x.药)}</td><td>${x.最小}</td><td><b>${x.中位}</b></td><td>${x.最大}</td><td>${x.次数}</td></tr>`;});
    h+=`</table></div>`;
  }

  h+=`<div class="card" style="margin-top:16px"><h2>相似病案</h2>
    <div class="note"><b>加权</b> = 共有标签数 + 共有舌脉数 × 1.5
      （舌脉给额外权重，因为脉是别人拿不到的维度）。<br>
      ⚠️ 这是<b>粗指标</b>：没设门槛，只共有一个标签的也会排上来；
      「共有」列才是你要看的——共有几个、是哪几个。</div>`;
  if(!d.相似病案.length) h+=`<span class="empty">无</span>`;
  d.相似病案.forEach(s=>{
    const 经 = (s.六经 && s.六经.length)
      ? `<span class="v"> ｜ 该案判：<b>${s.六经.map(esc).join("、")}</b></span>` : "";
    h+=`<div class="row"><span class="w">加权 ${s.加权}</span><span class="k">${esc(s.方名)}</span>
        <span class="v"> — ${esc(s.篇)} ${esc(s.页)}</span>${经}
        <div class="v">共有：${s.共有.map(esc).join("、")}</div>`;
    const f=s.附属||{};
    if(f.加减&&f.加减.length) h+=`<div class="v">　加减：${f.加减.map(esc).join("；")}</div>`;
    if(f.结构&&f.结构.length) h+=`<div class="v">　结构符合方：${f.结构.map(esc).join("；")}</div>`;
    if(f.方性&&f.方性.length) h+=`<div class="v">　方性：${f.方性.map(esc).join("；")}</div>`;
    h+=`</div>`;
  });
  h+=`</div>`;
  $("#结果").innerHTML=h;

  const pb=$("#pbtn");
  if(pb) pb.onclick=async()=>{
    pb.disabled=true; pb.textContent="判断中…（10~60 秒）";
    $("#pout").innerHTML='<span class="v">等两个模型回话…</span>';
    try{
      const r=await fetch("/判断",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({标签:d.标签, 候选:d.候选方.map(c=>c.方名),
                              原文:$("#四诊").value.trim()})});
      const j=await r.json();
      if(j.错误){
        $("#pout").innerHTML='<span class="v">出错：'+esc(String(j.错误).slice(0,400))+'</span>';
      }else{
        let s="";
        const 列=(o,f)=>Object.entries(o||{}).sort((a,b)=>b[1]-a[1])
            .map(([k,v])=>esc(k)+" "+f(v)).join("　");
        s+='<div class="row"><b>Jev</b><div class="v">'+列(j.jev,v=>(v*100).toFixed(0)+"%")+'</div></div>';
        s+='<div class="row"><b>DeepSeek</b><div class="v">'+列(j.ds,v=>v)+'</div></div>';
        if(j.整理后陈述) s+='<div class="note" style="margin:8px 0"><b>整理层输出 —— Jev 实际读到的就是这段</b>'+
            '<div style="margin-top:6px;color:var(--ink)">'+esc(j.整理后陈述)+'</div></div>';
        if(j.结论) s+='<div class="row '+(j.一致?"一致":"分歧")+'">'+esc(j.结论)+'</div>';
        if(j.jev错) s+='<div class="v">Jev 出错：'+esc(String(j.jev错).slice(0,200))+'</div>';
        if(j.ds错)  s+='<div class="v">DeepSeek 出错：'+esc(String(j.ds错).slice(0,200))+'</div>';
        $("#pout").innerHTML=s;
      }
    }catch(e){$("#pout").innerHTML='<span class="v">连不上：'+esc(e)+'</span>';}
    pb.disabled=false; pb.textContent="让两个模型判一下";
  };
}
</script></body></html>"""


if __name__ == "__main__":
    import uvicorn
    print("\n  面板： http://127.0.0.1:%d\n" % 端口)
    uvicorn.run(app, host="127.0.0.1", port=端口, log_level="warning")
