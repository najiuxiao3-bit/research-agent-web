# -*- coding: utf-8 -*-
"""
全局配置：优先读取本地 .env 文件，其次读取系统环境变量。
Key 只存在本地 .env，不写进代码、不随项目交付。
"""

import os
from pathlib import Path


def _load_env_file() -> None:
    """极简 .env 解析：KEY=VALUE，已有真实环境变量优先，不覆盖。"""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

# 模型 API 配置（OpenAI 兼容格式）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-flash")

# 可选：第三方搜索 API Key（当前默认走免费的 DuckDuckGo 即时答案，留空也能跑）
SEARCH_API_KEY = os.environ.get("SEARCH_API_KEY", "")

# 护栏参数
MAX_TURNS = 10          # 最多循环多少轮工具调用，防止死循环
REQUEST_TIMEOUT = 20    # 单次请求超时（秒）


def check_config() -> str:
    """在启动前检查必要配置，返回缺失项的提示；配置齐全返回空字符串。"""
    missing = []
    if not LLM_API_KEY:
        missing.append("LLM_API_KEY")
    if missing:
        return "缺少配置：" + ", ".join(missing) + "。请在 .env 或环境变量中填写后再运行。"
    return ""
