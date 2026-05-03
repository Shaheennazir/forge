"""
forge.llm — Pluggable LLM backend.

Supports: mmx, openai, anthropic, ollama.
Config: provider + model in ~/.forge/config.yaml or env vars.
"""

from __future__ import annotations
import os
import json
import time
import yaml
import structlog
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod
from forge.retry import RetryPolicy, APIError, retryable, delay

log = structlog.get_logger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".forge" / "config.yaml"

# Known OpenAI-compatible providers with their default base URLs
PROVIDER_DEFAULTS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
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
    "minimax": {
        "base_url": None,  # uses mmx CLI which reads ~/.mmx/config.json
        "env_key": "MINIMAX_API_KEY",
        "model": "MiniMax-M2.7",
        "docs": "https://platform.minimax.io",
    },
    "mmx": {
        "base_url": None,
        "env_key": "MINIMAX_API_KEY",
        "model": "MiniMax-M2.7",
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
        "base_url": "http://localhost:11434",
        "env_key": "OLLAMA_API_KEY",
        "model": "llama3",
        "docs": "https://ollama.com",
    },
}

# Known models per provider — shown in forge setup model picker
PROVIDER_MODELS = {
    "mmx": ["MiniMax-M2.7", "MiniMax-Text-01"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    "deepseek": ["deepseek-chat", "deepseek-coder"],
    "qwen": ["qwen-plus", "qwen-plus-ab", "qwen-turbo", "qwen-max"],
    "kimi": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    "glm": ["glm-4", "glm-4-plus", "glm-4-flash", "glm-4-air"],
    "anthropic": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
    "ollama": ["llama3", "llama3.1", "codellama", "mistral", "mixtral"],
}


@dataclass
class LLMConfig:
    provider: str  # mmx | openai | anthropic | ollama
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> LLMConfig:
    """Load LLM config from YAML or env vars."""
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        provider = data.get("provider", "mmx")
        model = data.get("model") or PROVIDER_DEFAULTS.get(provider, {}).get("model", "default")
        return LLMConfig(
            provider=provider,
            model=model,
            api_key=data.get("api_key") or _provider_env_key(provider),
            base_url=data.get("base_url") or PROVIDER_DEFAULTS.get(provider, {}).get("base_url"),
        )
    env_provider = os.getenv("FORGE_LLM_PROVIDER", "mmx")
    return LLMConfig(
        provider=env_provider,
        model=os.getenv("FORGE_LLM_MODEL") or PROVIDER_DEFAULTS.get(env_provider, {}).get("model", "default"),
        base_url=PROVIDER_DEFAULTS.get(env_provider, {}).get("base_url"),
    )


def _provider_env_key(provider: str) -> Optional[str]:
    known = PROVIDER_DEFAULTS.get(provider, {})
    if known:
        return os.getenv(known["env_key"])
    return None


class LLMBackend(ABC):
    @abstractmethod
    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str: ...

    @abstractmethod
    def complete_json(self, prompt: str, system: Optional[str] = None, **kwargs) -> dict: ...


class MMXBackend(LLMBackend):
    """MiniMax backend via mmx CLI (mmx text chat)."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.mmx_bin = Path.home() / ".hermes" / "node" / "bin" / "mmx"

    def _complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        import subprocess
        model = self.config.model or "MiniMax-M2.7"
        cmd = [
            str(self.mmx_bin), "text", "chat",
            "--model", model,
            "--message", prompt,
            "--output", "text",
        ]
        if system:
            cmd += ["--system", system]
        if max_tokens := kwargs.get("max_tokens"):
            cmd += ["--max-tokens", str(max_tokens)]
        if temperature := kwargs.get("temperature"):
            cmd += ["--temperature", str(temperature)]

        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=kwargs.get("timeout", 120),
        )
        if result.returncode != 0:
            log.error("mmx.complete.error", stderr=result.stderr[:500])
            raise RuntimeError(f"mmx failed: {result.stderr[:200]}")
        return result.stdout.strip()

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        policy = RetryPolicy(max_attempts=5)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._complete(prompt, system, **kwargs)
            except Exception as e:
                api_err = APIError(str(e))
                reason = retryable(api_err)
                if not reason or not policy.should_retry(attempt, api_err):
                    raise
                log.warning("llm.retry", attempt=attempt, reason=reason)
                time.sleep(delay(attempt, api_err) / 1000)

    def complete_json(self, prompt: str, system: Optional[str] = None, **kwargs) -> dict:
        text = self.complete(prompt, system, **kwargs)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            log.error("mmx.json_parse_error", text=text[:200], error=str(e))
            raise


class OpenAIBackend(LLMBackend):
    """OpenAI-compatible backend (also used for DeepSeek, Qwen, Kimi, GLM, etc.)."""

    def __init__(self, config: LLMConfig):
        self.config = config

    def _complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        import openai
        client_kwargs = {"api_key": self.config.api_key or os.getenv("OPENAI_API_KEY")}
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url
        client = openai.OpenAI(**client_kwargs)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=self.config.model, messages=messages, **kwargs
        )
        return resp.choices[0].message.content or ""

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        policy = RetryPolicy(max_attempts=5)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._complete(prompt, system, **kwargs)
            except Exception as e:
                api_err = APIError(str(e))
                reason = retryable(api_err)
                if not reason or not policy.should_retry(attempt, api_err):
                    raise
                log.warning("llm.retry", attempt=attempt, reason=reason)
                time.sleep(delay(attempt, api_err) / 1000)


class AnthropicBackend(LLMBackend):
    """Anthropic Claude backend."""

    def _complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=self.config.api_key or os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=self.config.model,
            max_tokens=kwargs.get("max_tokens", 4096),
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
            **kwargs
        )
        return resp.content[0].text

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        policy = RetryPolicy(max_attempts=5)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._complete(prompt, system, **kwargs)
            except Exception as e:
                api_err = APIError(str(e))
                reason = retryable(api_err)
                if not reason or not policy.should_retry(attempt, api_err):
                    raise
                log.warning("llm.retry", attempt=attempt, reason=reason)
                time.sleep(delay(attempt, api_err) / 1000)


class OllamaBackend(LLMBackend):
    """Ollama local model backend (http://localhost:11434)."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.base_url = (self.config.base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")

    def _complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        import requests
        model = self.config.model or "llama3"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if temperature := kwargs.get("temperature"):
            payload["temperature"] = temperature
        if max_tokens := kwargs.get("max_tokens"):
            payload["options"] = {"num_predict": max_tokens}

        resp = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=kwargs.get("timeout", 120),
        )
        if resp.status_code != 200:
            log.error("ollama.error", status=resp.status_code, body=resp.text[:200])
            raise RuntimeError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("response", "").strip()

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        policy = RetryPolicy(max_attempts=5)
        attempt = 0
        while True:
            attempt += 1
            try:
                return self._complete(prompt, system, **kwargs)
            except Exception as e:
                api_err = APIError(str(e))
                reason = retryable(api_err)
                if not reason or not policy.should_retry(attempt, api_err):
                    raise
                log.warning("llm.retry", attempt=attempt, reason=reason)
                time.sleep(delay(attempt, api_err) / 1000)


def ensure_config(path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Create a default config if none exists."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    default_provider = os.getenv("FORGE_LLM_PROVIDER", "mmx")
    default_model = os.getenv("FORGE_LLM_MODEL", "")
    import yaml
    data = {"provider": default_provider}
    if default_model:
        data["model"] = default_model
    with open(path, "w") as f:
        yaml.dump(data, f)


def create_backend(config: Optional[LLMConfig] = None) -> LLMBackend:
    ensure_config()
    config = config or load_config()
    # Normalize provider names
    provider = config.provider.lower()
    if provider == "minimax":
        provider = "mmx"
    if provider == "mmx":
        return MMXBackend(config)
    elif provider in ("openai", "deepseek", "qwen", "kimi", "glm"):
        return OpenAIBackend(config)
    elif provider == "anthropic":
        return AnthropicBackend(config)
    elif provider == "ollama":
        return OllamaBackend(config)
    else:
        raise ValueError(f"Unknown provider: {config.provider}")
