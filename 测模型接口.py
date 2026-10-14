# -*- coding: utf-8 -*-
"""换供应商自检 —— 只测「接口通不通 + 参数兼不兼容」，不测水平

换模型时最先死的往往不是能力，是参数：
  · 推理模型常拒绝 temperature=0 / response_format=json_object
  · 思考模式的流式字段可能是 reasoning_content 而不是 content
这里一条条试出来，省得在面板上撞 400 再猜。

它不碰数据库，随时可跑。

用法：py _测模型接口.py
跑的是 config.py 里的 LLM_*（没配就是 DeepSeek 默认）
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 模型判断 import DS_KEY, DS_URL, DS_MODEL, DS_MODEL_整理, DS_TEMP   # noqa: E402

题 = ('四诊：恶寒、无汗、脉浮、头项强痛。候选方：'
      'A 桂枝汤  B 麻黄汤  C 小柴胡汤  D 白虎汤。只输出 JSON：{"选项":"B"}')
头 = {"Authorization": "Bearer " + DS_KEY, "Content-Type": "application/json"}


def 试(名, 附加, 只看流=False):
    try:
        r = requests.post(DS_URL, headers=头, timeout=180, stream=只看流,
                          json={"model": DS_MODEL,
                                "messages": [{"role": "user", "content": 题}], **附加})
    except Exception as e:
        print("  × %-24s 请求异常 %r" % (名, e))
        return
    if r.status_code != 200:
        print("  × %-24s HTTP %s  %s" % (名, r.status_code, r.text[:150].replace("\n", " ")))
        return
    if 只看流:
        字 = 想 = 块 = 0
        for line in r.iter_lines():
            if not line or not line.startswith(b"data:"):
                continue
            p = line[5:].strip()
            if p == b"[DONE]":
                break
            try:
                d = json.loads(p)
            except Exception:
                continue
            块 += 1
            德 = (d.get("choices") or [{}])[0].get("delta") or {}
            字 += len(德.get("content") or "")
            想 += len(德.get("reasoning_content") or "")
        print("  ✓ %-24s 流式 %d 块 ／ 正文 %d 字 ／ 思考 %d 字" % (名, 块, 字, 想))
        return
    d = r.json()
    m = d["choices"][0]["message"]
    用 = d.get("usage") or {}
    print("  ✓ %-24s 正文 %-20r 用量 in/out %s/%s"
          % (名, (m.get("content") or "")[:18], 用.get("prompt_tokens"),
             用.get("completion_tokens")))
    if m.get("reasoning_content"):
        print("       └ 另带思考内容 %d 字（面板只读 content，不受影响）"
              % len(m["reasoning_content"]))


print("地址：%s" % DS_URL)
print("型号：判断层 %s ／ 整理层 %s" % (DS_MODEL, DS_MODEL_整理))
print("密钥：%s\n" % ("%s…（共 %d 位）" % (DS_KEY[:7], len(DS_KEY)) if DS_KEY else "（空）"))

print("① 裸调用")
试("无附加参数", {})
print("\n② 面板「问诊」走的流式")
试("stream + temperature=%g" % DS_TEMP, {"stream": True, "temperature": DS_TEMP}, 只看流=True)
print("\n③ 问ds（双判）真正用的组合")
试("temperature=0", {"temperature": 0})
试("response_format=json", {"response_format": {"type": "json_object"}})
试("temperature=0 + JSON", {"temperature": 0, "response_format": {"type": "json_object"}})
print("\n打 × 的那条就是要在 模型判断.py 的问ds() / 中医大脑面板.py 的 /问诊 里改的地方。")
