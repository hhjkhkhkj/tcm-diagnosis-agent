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
