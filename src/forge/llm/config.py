"""forge.llm.config — configuration loading and provider defaults."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

import yaml

from forge.llm.response import LLMResponse


# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------

PROVIDER_DEFAULTS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
        "model": "deepseek-chat",
        "docs": "https://platform.deepseek.com",
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env_key": "QWEN_API_KEY",
        "model": "qwen-plus",
        "docs": "https://help.aliyun.com",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "env_key": "KIMI_API_KEY",
        "model": "moonshot-v1-8k",
        "docs": "https://platform.moonshot.cn",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "env_key": "GLM_API_KEY",
        "model": "glm-4",
        "docs": "https://bigmodel.cn",
    },
    "minimax_openai": {
        "base_url": "https://api.minimax.chat/v1",
        "env_key": "MINIMAX_API_KEY",
        "model": "MiniMax-Text-01",
        "docs": "https://platform.minimax.io",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "docs": "https://platform.openai.com",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "env_key": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-6",
        "docs": "https://console.anthropic.com",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "env_key": "OLLAMA_API_KEY",
        "model": "llama3",
        "docs": "https://ollama.com",
    },
    "mmx": {
        "base_url": None,  # uses CLI wrapper
        "env_key": "MINIMAX_API_KEY",
        "model": "MiniMax-M2.7",
        "docs": "https://platform.minimax.io",
    },
}

PROVIDER_MODELS: dict[str, list[str]] = {
    "mmx": ["MiniMax-M2.7", "MiniMax-Text-01"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    "deepseek": ["deepseek-chat", "deepseek-coder"],
    "qwen": ["qwen-plus", "qwen-plus-ab", "qwen-turbo", "qwen-max"],
    "kimi": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    "glm": ["glm-4", "glm-4-plus", "glm-4-flash", "glm-4-air"],
    "anthropic": [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
    ],
    "ollama": ["llama3", "llama3.1", "codellama", "mistral", "mixtral"],
}


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = Path.home() / ".forge" / "config.yaml"


class LLMConfig:
    """LLM provider configuration."""

    __slots__ = ("provider", "model", "api_key", "base_url")

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def __repr__(self) -> str:
        key = self.api_key[:8] + "..." if self.api_key else None
        return f"LLMConfig(provider={self.provider!r}, model={self.model!r}, api_key={key!r})"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LLMConfig:
    """Load LLM config from YAML or env vars (env takes precedence)."""
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        provider = data.get("provider", "mmx")
        defaults = PROVIDER_DEFAULTS.get(provider, {})
        return LLMConfig(
            provider=provider,
            model=data.get("model") or defaults.get("model", "default"),
            api_key=_provider_env_key(provider) or data.get("api_key"),
            base_url=data.get("base_url") or defaults.get("base_url"),
        )

    env_provider = os.getenv("FORGE_LLM_PROVIDER", "mmx")
    defaults = PROVIDER_DEFAULTS.get(env_provider, {})
    return LLMConfig(
        provider=env_provider,
        model=os.getenv("FORGE_LLM_MODEL") or defaults.get("model", "default"),
        api_key=_provider_env_key(env_provider),
        base_url=defaults.get("base_url"),
    )


def _provider_env_key(provider: str) -> str | None:
    defaults = PROVIDER_DEFAULTS.get(provider, {})
    env_key = defaults.get("env_key")
    return os.getenv(env_key) if env_key else None


def ensure_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Create a default config file if none exists."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    default_provider = os.getenv("FORGE_LLM_PROVIDER", "mmx")
    default_model = os.getenv("FORGE_LLM_MODEL", "")
    data: dict = {"provider": default_provider}
    if default_model:
        data["model"] = default_model
    with open(path, "w") as f:
        yaml.dump(data, f)
