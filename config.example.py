# -*- coding: utf-8 -*-
"""
配置样板 —— 复制成 config.py 再填自己的密钥

    copy config.example.py config.py

**config.py 已经在 .gitignore 里，不会被提交。**
也可以不用这个文件，改走环境变量（同名）。
"""

# ── Jev：走 OpenRouter 的决策模型，原生输出概率（判断层主判）
#    https://openrouter.ai/keys
JEV_KEY = "sk-or-v1-在这里填你的密钥"

# ── DeepSeek：做交叉验证的第二判
#    https://platform.deepseek.com/api_keys
DS_KEY = "sk-在这里填你的密钥"


# ── 换模型（判断层 / 问诊推演层）───────────────────────────────
# 不填 = 继续用 DeepSeek（deepseek-flash 判断 / deepseek-v4-pro 整理）。
# 要换供应商就取消注释、四个一起填 —— **代码一个字不用改**。
# ⚠️ 只改 URL 不换型号，整理层会拿新地址去要 DeepSeek 的型号名，直接 400。
#
# 运行 `py 测模型接口.py` 可先验接口通不通、参数兼不兼容
# （推理模型常拒绝 temperature=0 / response_format=json_object）。
#
# 例：月之暗面官方 Kimi K3
# LLM_URL         = "https://api.moonshot.cn/v1/chat/completions"
# LLM_KEY         = "sk-在这里填你的密钥"
# LLM_MODEL       = "kimi-k3"
# LLM_MODEL_整理  = "kimi-k3"
#
# 例：阿里云百炼（同一个 key 也能叫 GLM-5.3 / Qwen3.8-Max，方便横评）
# LLM_URL         = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
# LLM_KEY         = "sk-在这里填你的密钥"
# LLM_MODEL       = "kimi-k3"
# LLM_MODEL_整理  = "kimi-k3"
