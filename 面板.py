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
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 检索 import 中医库, 展开, DB          # noqa: E402
from 模型判断 import (全判, 抽取_KEY, 抽取_URL, 抽取_MODEL,
                     推演_KEY, 推演_URL, 推演_MODEL, 推演_TEMP, 推演_推理,
                     安全底线)   # noqa: E402
from 问诊框架 import 框架, 造用户消息             # noqa: E402

端口 = 8020
app = FastAPI()
库 = 中医库()

# ── 从数据里算出来的两张索引（启动时算一次，之后每次检索直接用）──
# 为什么要它：库里算出的判别力/共现是**按案子算的**，不是静态规则 ——
# 所以每次请求都得用；但每次都重算又太慢，因此启动时算一遍。
#
#   因子指向：这个症状最指向哪一经（按全库 965 案的互信息，**不是教科书判据**）
#   共现索引：这条常跟谁同现（漏问一条，信息少一半）
因子指向 = {}
共现索引 = {}
try:
    import 因子分析 as _FA
    _案因子, _案经, _词频 = _FA.取()
    _剖, _各经案, _ = _FA.六经剖面(_案因子, _案经, _词频)
    for _经, _行 in _FA.互信息(_案因子, _案经, _词频, _各经案).items():
        for _f, _a, _c, _mi in _行:
            if _a < 3:        # 该经内出现 <3 次的，互信息是小样本假象（同 _层次模型）
                continue
            因子指向.setdefault(_f, []).append((_经, _mi))
    for _f in 因子指向:
        因子指向[_f].sort(key=lambda x: -x[1])
    for _x, _y, _n, _j in _FA.共现(_案因子, _词频, 上限=400, 门槛=6):
        共现索引.setdefault(_x, []).append((_y, _n, _j))
        共现索引.setdefault(_y, []).append((_x, _n, _j))
    for _f in 共现索引:
        共现索引[_f].sort(key=lambda x: -x[2])
    print("  因子索引：%d 条有六经指向 ／ %d 条有共现伙伴" % (len(因子指向), len(共现索引)))
except Exception as _e:
    print("[因子索引没算成，检索结果里那两段会缺]", str(_e)[:140])

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


class 新问诊请求(BaseModel):
    患者: str
    性别: str = ""
    年龄: str = ""
    内容: str


class 续聊请求(BaseModel):
    会话id: int
    内容: str


def 建会话表(con):
    """患者 → 会话（一次问诊）→ 消息（多轮）。

    为什么要这三层而不是一张表：
      患者要能**重复来**（下次同一个患者来了就调之前的记录）；
      一次问诊要能**多轮追问**（医生和 AI 来回讨论）；
      所以「谁」「哪一次」「哪句话」是三个维度，挤不进一张表。
    """
    con.executescript("""
        CREATE TABLE IF NOT EXISTS 患者(
            id INTEGER PRIMARY KEY, 姓名 TEXT UNIQUE, 性别 TEXT, 年龄 TEXT,
            备注 TEXT, 创建日期 TEXT);
        CREATE TABLE IF NOT EXISTS 会话(
            id INTEGER PRIMARY KEY, 患者id INTEGER, 标题 TEXT,
            创建时间 TEXT, 更新时间 TEXT);
        CREATE TABLE IF NOT EXISTS 消息(
            id INTEGER PRIMARY KEY, 会话id INTEGER, 角色 TEXT, 内容 TEXT,
            时间 TEXT, 用时秒 REAL, 命中标签 TEXT);
        CREATE INDEX IF NOT EXISTS ix_消息_会话 ON 消息(会话id);
        CREATE INDEX IF NOT EXISTS ix_会话_患者 ON 会话(患者id);
    """)


def 找或建患者(con, 姓名, 性别="", 年龄=""):
    """同名就复用 —— 下次同一个患者来了才调得到之前的记录"""
    姓名 = (姓名 or "").strip()
    if not 姓名:
        姓名 = "未具名"
    r = con.execute("SELECT id FROM 患者 WHERE 姓名=?", (姓名,)).fetchone()
    if r:
        # 补全之前没填的
        con.execute("UPDATE 患者 SET 性别=COALESCE(NULLIF(?,''),性别),"
                    " 年龄=COALESCE(NULLIF(?,''),年龄) WHERE id=?", (性别, 年龄, r[0]))
        return r[0]
    c = con.execute("INSERT INTO 患者(姓名,性别,年龄,创建日期) VALUES(?,?,?,?)",
                    (姓名, 性别, 年龄, datetime.now().strftime("%Y-%m-%d")))
    return c.lastrowid


def 取历史(con, 会话id, 字数上限=80000):
    """这个会话的**完整**往来消息 → 喂给模型的多轮上下文

    ⚠️ **带完整的**。不带上就变成每次从零开始，医生追问「那为什么不用真武汤」时
    它不记得刚才说了什么 —— 那就不是"讨论"，是每次重问一遍。

    按**字数**封顶而不是条数：一轮会诊正文就三四千字，按条数封（比如 40 条）
    等于没封；按字数才能兜住。从最近的往前累计，超了就停。
    """
    行 = list(con.execute("SELECT 角色,内容 FROM 消息 WHERE 会话id=? ORDER BY id",
                          (会话id,)))
    选, 用 = [], 0
    for 角色, 内容 in reversed(行):
        长 = len(内容 or "")
        if 选 and 用 + 长 > 字数上限:
            break
        选.append({"role": 角色, "content": 内容 or ""})
        用 += 长
    return list(reversed(选))


def 取患者档案(con, 患者id, 本次会话id=None, 字数上限=80000):
    """这个患者**以前几次问诊的完整往来** —— 新的一次要能接上旧的

    ⚠️ **带完整的，不做摘要。** 上一版只取每次 AI 回复的开头 600 字，
    结果医生问「上次你不是说要用真武汤吗」时，它看不到那段推演 ——
    摘要是有损的，而**医生的追问往往就落在被砍掉的那部分**。

    为防无限膨胀：从**最近的一次往前**累计，到字数上限为止（K3 上下文 1M token，
    8 万汉字约 5 万 token，留足余量）。砍掉的那部分要在正文里说明，别偷偷丢。
    """
    出, 用 = [], 0
    被砍 = 0
    for sid, 时 in con.execute(
            "SELECT id,创建时间 FROM 会话 WHERE 患者id=? AND id IS NOT ?"
            " ORDER BY id DESC", (患者id, 本次会话id)):
        段 = ["### %s 那次" % 时]
        for 角色, 内容 in con.execute(
                "SELECT 角色,内容 FROM 消息 WHERE 会话id=? ORDER BY id", (sid,)):
            段.append(("【医师】" if 角色 == "user" else "【会诊】") + (内容 or ""))
        文 = "\n\n".join(段)
        if 用 + len(文) > 字数上限:
            被砍 += 1
            break
        出.append(文)
        用 += len(文)
    return list(reversed(出)), 被砍


@app.get("/患者")
def 列患者():
    c = 连库()
    建会话表(c)
    q = ""
    rows = [dict(r) for r in c.execute("""
        SELECT p.id, p.姓名, p.性别, p.年龄,
               (SELECT COUNT(*) FROM 会话 s WHERE s.患者id=p.id) AS 问诊次数,
               (SELECT MAX(s.更新时间) FROM 会话 s WHERE s.患者id=p.id) AS 最近
        FROM 患者 p ORDER BY COALESCE(最近,'') DESC, p.id DESC LIMIT 200""")]
    c.commit()
    c.close()
    return {"列表": rows}


@app.get("/患者会话")
def 患者会话(患者id: int):
    """这个患者的全部记录 —— **按次分段**，医生一眼看出上次聊到哪"""
    c = 连库()
    建会话表(c)
    出 = []
    for s in c.execute("SELECT id,创建时间 FROM 会话 WHERE 患者id=? ORDER BY id",
                       (患者id,)):
        msgs = [dict(r) for r in c.execute(
            "SELECT 角色,内容,时间 FROM 消息 WHERE 会话id=? ORDER BY id", (s["id"],))]
        出.append({"会话id": s["id"], "时间": s["创建时间"], "消息": msgs})
    c.commit()
    c.close()
    return {"列表": 出}


自审提示 = """现在换个身份：**你是被请来复核这份方案的同行**，不是开方的人。

上面那份方案，就是你刚写的。**现在你要复核它。**

分两段走。**别重写整份方案**，也不要夸 —— 只讲问题。

## 一、逐项过（对着方案一条条核，机械地过）

1. **剂量** —— 每味对库内分布（库内 / 类推 / 泛化 分别是哪档）。偏离大的标出来。
   **但不判"超量"** —— 这套体系自己就超药典（细辛中位 10g），别拿药典那把尺子量。
   哪几味的量和这个人的**体质／年龄／病情**不匹配？偏大偏小都要说。
2. **煎法** —— 先煎／后下／包煎／烊化／冲服，**逐味过一遍**。
   （后下的常见的：砂仁、木香、薄荷；先煎的：附子、牡蛎、龙骨）
3. **毒性** —— 附子（**必须久煎**）、细辛、吴茱萸、马兜铃酸类。该说的说漏了没有？
4. **配伍** —— 库里的药对规则有没有提示冲突？十八反十九畏碰了没有？
5. **与前一方的对比** —— 这位患者以前开过方的话，加减逻辑站不站得住？每味去掉行不行？
   （以前没开过就跳过这条）

## 二、再回头看推理

6. **最弱的一环** —— 整份方案里哪一步的证据最薄？说清是**哪条症状/舌脉撑不住哪个结论**。
7. **我其实没排除的反向可能** —— 有没有哪个相反的方向（寒↔热、虚↔实、在表↔在里），提了一句就滑过去了？
8. **我漏问的** —— 还有哪个信息，**问到了就会改方**？（不是"可以补充"，是"会改方"）
9. **如果只让我改一处** —— 改哪一处？改成什么？为什么这一处最关键？

**某一条真查不出问题就写"这条查不出问题" —— 不要凑数。** 凑出来的问题比不报还坏。"""

自动自审 = True   # 首轮会诊出完，自动接一段自审（改 False 可关）


def _流式(消息表, 袋, 生成器=False):
    """跑一次流式调用：delta 边吐给前端，同时攒进 `袋`

    抽出来是因为现在要调**两次**（会诊 + 自审），复制一遍容易两边改漏。
    """
    r = requests.post(
        推演_URL,
        headers={"Authorization": "Bearer " + 推演_KEY, "Content-Type": "application/json"},
        json={"model": 推演_MODEL, "messages": 消息表,
              "temperature": 推演_TEMP,
              "reasoning_effort": 推演_推理,   # 深度旋钮，见 模型判断.py
              "stream": True},
        stream=True, timeout=900)
    r.raise_for_status()
    跳过 = 0
    for line in r.iter_lines():
        if not line:
            continue
        s = line.decode("utf-8", "ignore")
        if not s.startswith("data:"):
            continue
        p = s[5:].strip()
        if p == "[DONE]":
            break
        try:
            d = json.loads(p)
            delta = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
            if delta:
                袋.append(delta)
                yield delta
        except Exception:
            跳过 += 1
            continue
    if 跳过:
        # ⚠️ 以前这里是**纯静默跳过** —— 丢的那几个字不会报错，输出看着正常。
        #    2026-10-14 排查静默截断时补上（这类 bug 全是"看着没事"）
        print("[流式解析：跳过了 %d 行 —— 那几段内容丢了，但输出看起来是完整的]" % 跳过)


def _落库(会话id, 文, 标签):
    if not 文:
        return
    c = 连库()
    c.execute("INSERT INTO 消息(会话id,角色,内容,时间,命中标签) VALUES(?,?,?,?,?)",
              (会话id, "assistant", 文, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               json.dumps(标签, ensure_ascii=False)))
    c.execute("UPDATE 会话 SET 更新时间=? WHERE id=?",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 会话id))
    c.commit()
    c.close()


def 跑推演(会话id, 消息表, 医生这句, 标签, 首轮=False):
    """把 [历史 + 这一轮] 发给模型，边收边吐，收完落库

    为什么要带历史：不带的话医生追问「那为什么不用真武汤」时，
    它不记得刚才说了什么 —— 那就不是"讨论"，是每次重新问一遍。

    `首轮=True` 时，方案出完**自动接一段自审** —— 换个身份挑自己的错。
    深度不只靠"想得久"（那是 `推演_推理` 的事，实测 high 就是顶），
    更靠**换个视角再看一遍**：第二遍看的是已经写出来的东西，跟第一遍的视角完全不同。

    **安全底线在这里统一注入** —— 两个调用方（新问诊 / 续聊）都覆盖到。
    2026-10-14 补：原来推演层拿不到安全规则（只有判断层的 Jev/DeepSeek 有），
    而**它才是写方案的那个** —— 「生附子必须久煎」这些它一条都不知道。
    """
    底 = 安全底线()
    if 底 and 消息表 and 消息表[0].get("role") == "system":
        消息表 = [{"role": "system", "content": 消息表[0]["content"] + "\n\n" + 底}] + 消息表[1:]

    def 生成():
        出 = []
        try:
            yield from _流式(消息表, 出)
        except Exception as e:
            yield "\n\n[出错] " + str(e)[:300]
        文 = "".join(出)
        _落库(会话id, 文, 标签)
        if not (文 and 首轮 and 自动自审):
            return
        # ── 二审：换个身份挑错 ──
        yield "\n\n\n---\n\n# ⚙️ 自审 —— 换个身份复核这份方案\n\n"
        二 = []
        try:
            yield from _流式(消息表 + [{"role": "assistant", "content": 文},
                                     {"role": "user", "content": 自审提示}], 二)
        except Exception as e:
            yield "\n\n[自审出错] " + str(e)[:300]
        _落库(会话id, "## ⚙️ 自审\n\n" + "".join(二), 标签)
    return StreamingResponse(生成(), media_type="text/plain; charset=utf-8")


@app.post("/新问诊")
def 新问诊(请求数据: 新问诊请求):
    """开一次新的问诊：找/建患者 → 建会话 → 检索喂料 → 流式会诊"""
    c = 连库()
    建会话表(c)
    患者id = 找或建患者(c, 请求数据.患者, 请求数据.性别, 请求数据.年龄)
    会话id = c.execute("INSERT INTO 会话(患者id,标题,创建时间,更新时间) VALUES(?,?,?,?)",
                     (患者id, (请求数据.内容 or "")[:20],
                      datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                     ).lastrowid
    旧, 被砍 = 取患者档案(c, 患者id, 本次会话id=会话id)
    c.execute("INSERT INTO 消息(会话id,角色,内容,时间) VALUES(?,?,?,?)",
              (会话id, "user", 请求数据.内容, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()

    标签 = 抽标签(请求数据.内容, [])
    材料 = 造材料(标签)
    本轮 = "## 【医师提供的原始观察】\n\n（他给的是**看到什么**，不是结论。定证是你的活。）\n\n" + 请求数据.内容
    if 旧:
        本轮 += "\n\n## 【这位患者以前的完整记录】\n\n" + "\n\n".join(旧)
        本轮 += ("\n\n**新的一次要接上旧的一次** —— 若这一次的判断跟上次不同，"
                 "要说清为什么变了（是新信息变了，还是上次判断有误）。\n")
        if 被砍:
            本轮 += "（注：更早还有 %d 次记录因为太长没放进来。）\n" % 被砍
    本轮 += "\n\n## 【库检索结果】\n\n" + 材料
    本轮 += ("\n\n---\n\n按体例七步推演。**第二步「逐条注解」、第三步「体质分析」、"
             "第七步「不确定处」都不能省。**\n每一步的判断都要写出**推理链**（凭什么这么想），不要只给结论。\n")
    c.close()
    消息表 = [{"role": "system", "content": 框架}, {"role": "user", "content": 本轮}]
    return 跑推演(会话id, 消息表, 请求数据.内容, 标签, 首轮=True)


@app.post("/续聊")
def 续聊(请求数据: 续聊请求):
    """在这次问诊里继续追问 —— 带上本会话的历史，**重新检索一次**（医生可能补了新症状）"""
    c = 连库()
    建会话表(c)
    历史 = 取历史(c, 请求数据.会话id)
    if not 历史:
        c.close()
        return {"错误": "找不到这个会话"}
    会话 = c.execute("SELECT 患者id FROM 会话 WHERE id=?", (请求数据.会话id,)).fetchone()
    患者id = 会话[0] if 会话 else None
    # 医生在这次会话里说过的全部话 —— 用它重新检索，补的症状能带进来
    累计 = " ".join(m["content"] for m in 历史 if m["role"] == "user")
    标签 = 抽标签(累计 + " " + 请求数据.内容, [])
    材料 = 造材料(标签)
    c.execute("INSERT INTO 消息(会话id,角色,内容,时间) VALUES(?,?,?,?)",
              (请求数据.会话id, "user", 请求数据.内容,
               datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    c.commit()
    c.close()
    本轮 = 请求数据.内容 + "\n\n---\n\n【库检索结果（本次会话累计）】\n\n" + 材料
    消息表 = [{"role": "system", "content": 框架}] + 历史 + [{"role": "user", "content": 本轮}]
    return 跑推演(请求数据.会话id, 消息表, 请求数据.内容, 标签)




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
    """让模型从受控词表里挑标签 —— 硬匹配认不出的（"大便偏溏"↔"大便溏"）它能对上

    ⚠️ **必须带 `reasoning_effort: "low"`。** 2026-10-14 实测同一句话：
        带 low   →  4.2 秒 ／ 输出  149 token ／ 抽出 15 个标签
        不带     →  197 秒 ／ 输出 8780 token ／ 抽出 15 个标签（**结果一样**）
    挑词不需要推理，不设这个参数模型会走默认的 high 档狂想 —— **差 47 倍**，
    而且它是"每次问诊都要调"的接口，慢六分钟等于整个界面卡死。

    超时/失败仍要重试：百炼偶尔抖（失败是静默的，只退回硬匹配，输出照常出）。
    """
    词 = 词表["舌脉"] + 词表["症状"]
    p = ("下面是一位中医师对病人的四诊描述。请从【可选标签】里挑出这段话**确实包含**的标签。\n"
         "规则：只从给定标签里挑，不要自己造词；没提到的绝对不要挑；"
         "同一个意思换种说法也要挑出来（例：\"大便偏溏\"→\"大便溏\"，\"苔白\"→\"舌苔白\"，"
         "\"脉沉细\"→\"沉脉\"+\"细脉\"）。\n"
         '只输出 JSON：{"标签": ["…","…"]}，不要任何别的文字。\n\n'
         "【可选标签】\n" + "、".join(词) + "\n\n"
         "【四诊描述】\n" + 文本)
    for 试 in range(3):
        try:
            r = requests.post(
                抽取_URL,
                headers={"Authorization": "Bearer " + 抽取_KEY, "Content-Type": "application/json"},
                json={"model": 抽取_MODEL, "messages": [{"role": "user", "content": p}],
                      "temperature": 0,
                      "reasoning_effort": "low",   # ← 见上：不带这个会慢 47 倍
                      "response_format": {"type": "json_object"}},
                timeout=120)
            r.raise_for_status()
            出 = json.loads(r.json()["choices"][0]["message"]["content"]).get("标签", [])
            合法 = set(词)
            return [t for t in 出 if t in 合法]
        except Exception as e:
            if 试 == 2:
                print("[模型抽标签失败，三次都没成，退回硬匹配]", str(e)[:120])
            else:
                time.sleep(3 * (试 + 1))
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

    # ── 数据算出来的两条：六经指向 / 建议顺手问 ──
    # 这两条不是静态规则，是按**全库 965 案**现算的（见文件顶部那两张索引）。
    # 放这里而不是写进提示词：它们**跟着案子变**，写进提示词就成死条文了。
    指 = [(t, 因子指向[t][:2]) for t in 标签 if t in 因子指向]
    if 指:
        out.append("\n### 这些症状各自指向哪一经")
        out.append("（按全库 965 案算出来的判别力排序，**不是教科书判据** ——"
                   "教科书那 7 条判据在本库上 5 条一条都命中不了。没上榜的症状说明它不分经，不用管）")
        for t, vs in 指[:14]:
            out.append("- %s → %s" % (t, "、".join("%s(%.3f)" % (j, m) for j, m in vs)))

    已有 = set(标签)
    问 = []
    for t in 标签:
        for y, n, j in 共现索引.get(t, []):
            if y not in 已有 and j >= 0.3:
                问.append((t, y, n, j))
    问.sort(key=lambda x: (-x[3], -x[2]))
    if 问:
        out.append("\n### 建议顺手问（这些常跟已有的症状同现 —— **漏问一条，信息少一半**）")
        for t, y, n, j in 问[:10]:
            out.append("- 已有「%s」→ **问 %s**（同现 %d 案，J=%.2f）" % (t, y, n, j))

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
                      "messages": [{"role": "system", "content": 框架 + (
                          ("\n\n" + 安全底线()) if 安全底线() else "")},
                                   {"role": "user", "content": 消息}],
                      "temperature": 推演_TEMP,
                      "reasoning_effort": 推演_推理,
                      "stream": True},
                stream=True, timeout=600)
            r.raise_for_status()
            跳过 = 0
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
                    跳过 += 1
                    continue
            if 跳过:
                print("[流式解析：跳过了 %d 行 —— 那几段内容丢了]" % 跳过)
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
      <button class="tab" data-m="会话">会话</button>
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
      <textarea id="四诊" placeholder="把四诊写在这里，自由文字就行。&#10;&#10;⚠️ 只写你「看到什么」（舌、脉、症状、病史），别写结论 —— 定证是它的活。&#10;&#10;例：&#10;男，42岁。怕冷，手脚冰凉，腹部怕凉，遇冷即腹泻，泻后舒服。大便一天2-3次偏溏。夜尿2-3次。腰膝酸冷。&#10;&#10;舌：舌体胖大、边有齿痕，舌质淡，舌苔白滑，中根部略厚&#10;脉：右寸关沉细弱，尺部尤弱；左关略弦"></textarea>
      <div class="v" style="margin:10px 0 12px;font-size:12px">不用勾标签——系统会自己从受控词表里抽。缺什么它会指出来。</div>
      <button id="抽" class="mini" style="width:100%;padding:12px">抽取标签 → 我来核对</button>
      <button id="问">问 诊</button>
    </div>

    <div id="区会话" hidden>
      <div class="v" style="font-size:12px;margin-bottom:10px">
        选一个患者 → 右边显示他<b>历次问诊的全部记录</b>（按次分段）。
        <b>同一个患者再来，直接接着上次聊。</b>
      </div>
      <input id="搜患者" placeholder="搜患者姓名…" style="margin-bottom:8px">
      <div id="患者列表" style="max-height:210px;overflow:auto"></div>
      <div style="border-top:1px solid var(--line);margin:12px 0 8px;padding-top:10px">
        <div class="v" style="font-size:12px;margin-bottom:6px"><b>开一次新问诊</b>（患者姓名填已存在的 = 接上次）</div>
        <div style="display:flex;gap:6px;margin-bottom:6px">
          <input id="新患者" placeholder="患者姓名" style="flex:2">
          <input id="新性别" placeholder="性别" style="flex:1">
          <input id="新年龄" placeholder="年龄" style="flex:1">
        </div>
        <textarea id="新四诊" placeholder="四诊原始观察（只写看到的，别写结论）" style="height:80px"></textarea>
        <button id="开新问诊" style="width:100%;padding:11px;margin-top:6px">开 始 问 诊</button>
      </div>
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
    $("#区会话").hidden = (m!=="会话");
    $("#区病案").hidden = (m!=="病案");
    if(m==="病案") 载病案();
    if(m==="会话") 载患者();
    $("#结果").innerHTML = (m==="问诊")
      ? '<div class="card"><span class="empty">左边写好四诊，再按下面的「问 诊」。</span></div>'
      : (m==="会话" ? '<div class="card"><span class="empty">左边选一个患者，看他的历次记录。</span></div>' : "");
  };
});

// 我的病案 —— 列表 + 回访
// ── 会话：患者 → 历次问诊 → 多轮讨论 ──
async function 载患者(){
  const r=await fetch("/患者"); const d=await r.json();
  const q=($("#搜患者").value||"").trim();
  const list=(d.列表||[]).filter(p=>!q||(p.姓名||"").includes(q));
  if(!list.length){
    $("#患者列表").innerHTML='<div class="empty" style="padding:10px 0">还没有患者。下面开一次新问诊就会建。</div>';return;
  }
  $("#患者列表").innerHTML=list.map(p=>
    `<div class="row" style="cursor:pointer" onclick="选患者(${p.id},'${esc(p.姓名)}')">
       <span class="k">${esc(p.姓名)}</span>
       <span class="v"> — ${esc(p.性别||"")} ${esc(p.年龄||"")} ｜ 问诊 ${p.问诊次数} 次 ｜ 最近 ${esc(p.最近||"")}</span>
     </div>`).join("");
}
$("#搜患者").oninput=载患者;

window.选患者=async(id,姓名)=>{
  const r=await fetch("/患者会话?患者id="+id); const d=await r.json();
  let h=`<div class="card"><h2>${esc(姓名)} · 历次问诊</h2><div class="note">这位患者的全部记录，按次分段。下面每次末尾都能接着追问。</div>`;
  if(!(d.列表||[]).length) h+='<span class="empty">这个患者还没有记录。</span>';
  (d.列表||[]).forEach((s,i)=>{
    h+=`<div style="margin:16px 0 6px;padding-top:10px;border-top:1px solid var(--line)">
        <b>第 ${i+1} 次问诊</b> <span class="v">${esc(s.时间)}</span></div>`;
    s.消息.forEach(m=>{
      h+= m.角色==="user"
        ? `<div class="row"><span class="w">问</span><div class="v">${esc(m.内容)}</div></div>`
        : `<div class="row" style="background:#faf8f5"><span class="w">方案</span><div class="v stream">${esc(m.内容)}</div></div>`;
    });
    h+=`<div style="margin-top:8px;display:flex;gap:6px">
        <input id="续${s.会话id}" placeholder="接着这次问诊追问…" style="flex:1">
        <button class="mini" onclick="续聊(${s.会话id})" style="padding:6px 14px">追问</button></div>`;
  });
  h+=`</div>`;
  $("#结果").innerHTML=h;
};

window.续聊=async(会话id)=>{
  const box=$("#续"+会话id); const v=(box.value||"").trim();
  if(!v){alert("写点要问的");return;}
  box.value="";
  $("#结果").insertAdjacentHTML("beforeend",
    `<div class="row"><span class="w">问</span><div class="v">${esc(v)}</div></div>
     <div class="row" style="background:#faf8f5"><span class="w">方案</span><div class="v stream" id="流${会话id}"></div></div>`);
  const 流=$("#流"+会话id);
  try{
    const r=await fetch("/续聊",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({会话id:会话id,内容:v})});
    const rd=r.body.getReader(); const dec=new TextDecoder();
    while(true){const {done,value}=await rd.read(); if(done)break;
      流.textContent+=dec.decode(value,{stream:true});}
  }catch(e){流.textContent="出错："+e;}
};

$("#开新问诊").onclick=async()=>{
  const 患者=($("#新患者").value||"").trim();
  const 内容=($("#新四诊").value||"").trim();
  if(!患者){alert("填个患者姓名 —— 填已存在的名字就会接上次的记录");return;}
  if(!内容){alert("把四诊写进去");return;}
  const b=$("#开新问诊"); b.disabled=true; b.textContent="会诊中…（2~5 分钟）";
  $("#结果").innerHTML='<div class="card"><span class="empty">正在检索库、推演中…</span></div>'+
    `<div class="row"><span class="w">问</span><div class="v">${esc(内容)}</div></div>
     <div class="row" style="background:#faf8f5"><span class="w">方案</span><div class="v stream" id="流新"></div></div>`;
  const 流=$("#流新");
  try{
    const r=await fetch("/新问诊",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({患者:患者,性别:$("#新性别").value.trim(),年龄:$("#新年龄").value.trim(),内容:内容})});
    const rd=r.body.getReader(); const dec=new TextDecoder();
    while(true){const {done,value}=await rd.read(); if(done)break;
      流.textContent+=dec.decode(value,{stream:true});}
  }catch(e){流.textContent="出错："+e;}
  b.disabled=false; b.textContent="开 始 问 诊";
  $("#新四诊").value="";
  载患者();
  选患者(0,患者);   // 名字给用户看；真正加载在下面重取
  const r2=await fetch("/患者"); const d2=await r2.json();
  const 中=(d2.列表||[]).find(p=>p.姓名===患者);
  if(中) 选患者(中.id, 患者);
};

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
