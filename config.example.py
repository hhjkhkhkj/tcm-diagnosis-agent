# -*- coding: utf-8 -*-
"""
配置样板 —— 复制成 config.py 再填自己的密钥

    copy config.example.py config.py

**config.py 已经在 .gitignore 里，不会被提交。**
也可以不用这个文件，改走环境变量（同名）。

⚠️ 键名以 `模型判断.py` 顶部的 `_取(...)` 为准 —— **本文件里的每个键名，代码都真的会去读**。
（2026-10-14 修：本文件原先写的是 `LLM_URL / LLM_KEY / LLM_MODEL`，而代码读的是
 `推演_*` / `抽取_*` —— 照旧样板配完，**代码会完全忽略它、悄悄退回 DeepSeek，且不报错**。）
"""

# ══════════════════════════════════════════════════════════════════
# 必须的两个：判断层（Jev 主判 + DeepSeek 第二判）
# ══════════════════════════════════════════════════════════════════

# ── Jev：走 OpenRouter 的决策模型，**原生输出概率**（判断层主判）
#    为什么是它：通用 LLM 只能自报置信度，而实测自报不可信。要概率就得用输出概率的模型。
#    https://openrouter.ai/keys
JEV_KEY = "sk-or-v1-在这里填你的密钥"

# ── DeepSeek：交叉验证的第二判（要的是"独立第二意见"，不是第二强的模型）
#    https://platform.deepseek.com/api_keys
DS_KEY = "sk-在这里填你的密钥"


# ══════════════════════════════════════════════════════════════════
# 可选的两个：推演层 / 抽取层
#   ⚠️ **不填也不会崩** —— 代码是 `_取("推演_KEY") or DS_KEY` 这种写法，
#      缺了会自动退回 DeepSeek。但那样推演质量会降（实测差一档），所以建议填。
#   ⚠️ 这两层是**故意拆开**的：同一个模型换了 URL 要四个键一起改，
#      只改 URL 不改型号，会拿新地址去要旧型号名 → 直接 400。
# ══════════════════════════════════════════════════════════════════

# ── 推演层：面板「问诊」的完整推演（流式）
#    实测：`reasoning_effort` 的深度天花板就在 high（low 734 字思考 / high 2216 / max 2350）
推演_URL   = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
推演_KEY   = "sk-在这里填你的密钥"
推演_MODEL = "kimi-k3"
推演_推理  = "high"          # low / high / max

# ── 抽取层：面板「抽标签」+ 批量抽取
#    实测：同一提示下 k3 输出 201 token/例、6 秒；flash 是 4798 token/42 秒。
#    **思考模型的思考链按输出计费，这条活它想得少，反而又省又快。**
抽取_URL   = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
抽取_KEY   = "sk-在这里填你的密钥"
抽取_MODEL = "kimi-k3"


# ── 通用 ──
# 推理模型常拒绝 temperature≠0（K3 实测只认 0，填 0.3 直接 400）。
# 所以统一取 0：既兼容，又让抽取可复现。
LLM_TEMP = 0


# ══════════════════════════════════════════════════════════════════
# 换供应商怎么换
# ══════════════════════════════════════════════════════════════════
# 只要改上面那 6 个（推演_URL/KEY/MODEL + 抽取_URL/KEY/MODEL），**代码一个字不用改**。
#
# 例 · 月之暗面官方 K3：
#   推演_URL = "https://api.moonshot.cn/v1/chat/completions"
#   推演_MODEL = "kimi-k3"
#
# 例 · 阿里云百炼（同一个 key 还能叫 glm-5.3-prime / qwen3.8-max，方便横评）：
#   推演_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
#   推演_MODEL = "kimi-k3"
#
# ⚠️ 换之前先跑 `py 测模型接口.py`（5 条探针，不碰库）——
#    **换模型最先死的不是能力，是参数**（temperature / response_format / reasoning_effort
#    各家接受度不一样）。跑完了再改 config，省得面板当天就废。
