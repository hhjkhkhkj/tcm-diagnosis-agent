# -*- coding: utf-8 -*-
"""
模型判断 —— Jev / DeepSeek 的调用封装

两个模型都做同一件事：给一组四诊 + 一组候选方，各自独立挑一个。
一致 → 高可信；分歧 → 需人工复核（27 条实测：一致 88% / 分歧 45%）

注意：两个模型的问法不同，这是有意保留的——
    Jev 用它的原生 noul 题型（逐个"是否应当选用X"，输出概率，刻度偏保守）
    DeepSeek 用打分（0~100，刻度偏自信）
所以**不要横向比两者的分数**，只比"谁被选为最高"。
"""
import json
import os
import re

import requests

# 密钥从 config.py 读（该文件在 .gitignore 里，不进仓库）；也可走环境变量。
# 样板见仓库里的 config.example.py
try:
    import config as _cfg
except ImportError:
    _cfg = None


def _取(名):
    v = getattr(_cfg, 名, "") if _cfg else ""
    return v or os.environ.get(名, "")


JEV_KEY = _取("JEV_KEY")
JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "~typesafe/jev-latest"

DS_KEY = _取("DS_KEY")
DS_URL = "https://api.deepseek.com/v1/chat/completions"
DS_MODEL = "deepseek-flash"

# 整理层用更强的模型：它要吃「医师的原话」，比改写标签难
DS_MODEL_整理 = "deepseek-v4-pro"


def canon(name):
    """合方顺序无关 —— 真武汤合柴胡桂枝汤 == 柴胡桂枝汤合真武汤"""
    s = re.sub(r"[\s\[\]【】]", "", name or "")
    for a, b in [("菱", "姜"), ("荣胡", "柴胡"), ("乐胡", "柴胡"), ("棠胡", "柴胡"),
                 ("挂枝", "桂枝"), ("魔贯", "麻黄"), ("趣痰", "逐瘀"), ("渴中", "温中"),
                 ("于姜", "干姜"), ("千姜", "干姜")]:
        s = s.replace(a, b)
    if "合" in s:
        s = "合".join(sorted(s.split("合")))
    return s


def 造state(症状, 舌脉):
    return ("患者四诊信息：\n症状：" + "、".join(症状) +
            "\n舌象：" + "、".join(x for x in 舌脉 if "脉" not in x) +
            "\n脉象：" + "、".join(x for x in 舌脉 if "脉" in x))


def 整理成陈述(症状, 舌脉, 原文="", timeout=150):
    """把「医师原话 + 已核对的标签」整理成一段规范的四诊陈述

    为什么要给原文：**抽取标签是有损的。**
        原话"遇冷或吃凉的即腹泻，泻后反觉舒服" → 标签只剩「腹泻后较舒畅」，
        "遇冷即"这个**辨寒的关键前提**丢了。
    为什么要给标签：标签是医师核对过的，代表"这份信息已被确认"。

    ⚠️ 关键是"只整理，不添加"：模型一旦在这层脑补病机，后面 Jev 判的就是脑补出来的东西。
    """
    if not (症状 or 舌脉 or (原文 or "").strip()):
        return ""
    p = ("把下面这份中医病案整理成一段**规范的病历式陈述**，供另一位医师判读。\n\n"
         "硬要求：\n"
         "1. **只整理，不添加** —— 不推断病机、不补充症状、不下诊断结论。"
         "原文没说的，一个字都不要写。\n"
         "2. 换成中医书面语（例：\"大便偏溏\"→\"大便溏薄\"；\"怕冷\"→\"畏寒\"；"
         "\"手脚冰凉\"→\"四肢不温\"）。\n"
         "3. **保留原文里的前提条件** —— 例：\"遇冷即腹泻\"里的\"遇冷即\"必须留下，"
         "那是辨寒的关键，不能简化掉。\n"
         "4. 分四段组织：主症 / 兼症 / 舌象 / 脉象。没给的写「未提供」。\n"
         "5. 只输出陈述本身，不要前后缀、不要解释。\n\n"
         "【医师原话】\n" + ((原文 or "").strip() or "（未提供）") + "\n\n"
         "【已核对的标签】（医师确认过的，与上面原话有出入时以这里为准）\n"
         "症状：" + ("、".join(症状) if 症状 else "（未提供）") + "\n"
         "舌象：" + ("、".join(x for x in 舌脉 if "脉" not in x) or "（未提供）") + "\n"
         "脉象：" + ("、".join(x for x in 舌脉 if "脉" in x) or "（未提供）"))
    try:
        r = requests.post(
            DS_URL,
            headers={"Authorization": "Bearer " + DS_KEY, "Content-Type": "application/json"},
            json={"model": DS_MODEL_整理, "messages": [{"role": "user", "content": p}],
                  "temperature": 0},
            timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("[整理层失败，退回标签堆]", str(e)[:80])
        return ""


def 问jev(候选, 症状, 舌脉, timeout=90, 整理好的=""):
    """传入 整理好的 就用它当 state；否则回退成标签堆"""
    state = ("患者四诊信息：\n" + 整理好的) if 整理好的 else 造state(症状, 舌脉)
    qs = {}
    for i, o in enumerate(候选):
        qs["o%d" % i] = {
            "type": "noul",
            "instructions": ("这位患者是中医门诊病案。根据四诊信息，这个病案是否应当选用「%s」？"
                             "四诊符合该方的适应证就判「是」，不符合或明显不如其他方合适就判「否」。" % o),
        }
    r = requests.post(
        JEV_URL,
        headers={"Authorization": "Bearer " + JEV_KEY, "Content-Type": "application/json"},
        json={"model": JEV_MODEL, "state": state, "questions": qs},
        timeout=timeout)
    r.raise_for_status()
    a = r.json()["answers"]
    return {候选[i]: a["o%d" % i]["noul"] for i in range(len(候选))}


def 问ds(候选, 症状, 舌脉, timeout=120):
    p = ("你是中医经方专家。根据四诊信息，给每个候选方打 0~100 的匹配分。\n"
         "只输出 JSON：{\"方名\": 分数}，不要任何别的字。\n\n"
         + 造state(症状, 舌脉) + "\n\n候选方：\n" + "\n".join("- " + o for o in 候选))
    r = requests.post(
        DS_URL,
        headers={"Authorization": "Bearer " + DS_KEY, "Content-Type": "application/json"},
        json={"model": DS_MODEL, "messages": [{"role": "user", "content": p}],
              "temperature": 0, "response_format": {"type": "json_object"}},
        timeout=timeout)
    r.raise_for_status()
    d = json.loads(r.json()["choices"][0]["message"]["content"])
    # DeepSeek 可能改写方名（合方顺序），归一回候选
    归 = {}
    for k, v in d.items():
        for c in 候选:
            if canon(k) == canon(c):
                归[c] = float(v)
                break
    return 归


def 全判(候选, 症状, 舌脉, 原文=""):
    """两步：
       ① 整理层 —— 强模型把「医师原话 + 已核对标签」写成规范的四诊陈述
                    （判断模型擅长判断，不擅长整理）
       ② 判断层 —— Jev + DeepSeek 各判一次，比一致/分歧
    """
    out = {"候选": 候选}

    # ① 整理层
    陈述 = 整理成陈述(症状, 舌脉, 原文)
    out["整理后陈述"] = 陈述 or "（整理层没跑成，Jev 拿到的是原始标签堆）"

    # ② 判断层
    try:
        jp = 问jev(候选, 症状, 舌脉, 整理好的=陈述)
        out["jev"] = jp
        out["jev选"] = max(jp, key=jp.get)
    except Exception as e:
        out["jev错"] = str(e)[:200]
    try:
        dp = 问ds(候选, 症状, 舌脉)
        out["ds"] = dp
        out["ds选"] = max(dp, key=dp.get) if dp else None
    except Exception as e:
        out["ds错"] = str(e)[:200]

    if out.get("jev选") and out.get("ds选"):
        同 = canon(out["jev选"]) == canon(out["ds选"])
        out["一致"] = 同
        out["结论"] = "两个模型一致 → 高可信" if 同 else "两个模型分歧 → ⚠️ 需人工复核"
    return out


if __name__ == "__main__":
    r = 全判(["柴胡桂枝汤合真武汤", "越婢加术附汤", "葛根汤合当归芍药散", "桂枝加龙骨牡蛎汤"],
             ["心烦", "夜尿", "脚冷"], ["舌瘦小", "舌有齿痕", "舌苔白", "弦脉"])
    print(json.dumps(r, ensure_ascii=False, indent=1))
