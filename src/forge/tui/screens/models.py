"""
forge.tui.screens.models — ModelPicker screen.

A DataTable listing all available models grouped by provider.
Selecting a row sets the active model and returns to the chat.
"""

from textual.app import ComposeResult
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Static


class ModelPicker(Screen):
    """
    Model selection screen with DataTable.

    Columns: Provider, Model, Description
    """

    class ModelSelected(Message):
        def __init__(self, provider: str, model: str) -> None:
            super().__init__()
            self.provider = provider
            self.model = model

    CSS = """
    ModelPicker {
        layout: vertical;
    }

    # model-title {
        height: 3;
        content-align: center middle;
        text-style: bold;
    }
    """

    def __init__(self, on_select: callable = None, **kwargs):
        super().__init__(**kwargs)
        self._on_select = on_select

    def compose(self) -> ComposeResult:
        yield Static("Model Picker  (↑↓ navigate, Enter select, Esc close)", id="model-title")
        yield DataTable()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Provider", "Model", "Description")

        rows = [
            # OpenAI
            ("openai",           "gpt-4o",              "GPT-4o — latest flagship"),
            ("openai",           "gpt-4o-mini",         "GPT-4o mini — faster, cheaper"),
            ("openai",           "gpt-4-turbo",         "GPT-4 Turbo — fast large context"),
            ("openai",           "gpt-3.5-turbo",       "GPT-3.5 — legacy, very fast"),
            # Anthropic
            ("anthropic",        "claude-sonnet-4-6",    "Claude Sonnet 4 — best overall"),
            ("anthropic",        "claude-opus-4-6",      "Claude Opus 4 — most capable"),
            ("anthropic",        "claude-3-5-sonnet-latest", "Claude 3.5 Sonnet — balanced"),
            ("anthropic",        "claude-3-5-haiku-latest",  "Claude 3.5 Haiku — fast"),
            # DeepSeek
            ("deepseek",         "deepseek-chat",        "DeepSeek Chat — low cost"),
            ("deepseek",         "deepseek-coder",       "DeepSeek Coder — code specialized"),
            # Qwen
            ("qwen",             "qwen-plus",            "Qwen Plus — Alibaba"),
            ("qwen",             "qwen-turbo",           "Qwen Turbo — fast"),
            # Kimi / Moonshot
            ("kimi",             "moonshot-v1-8k",       "Moonshot V1 8k context"),
            ("kimi",             "moonshot-v1-32k",      "Moonshot V1 32k context"),
            # GLM
            ("glm",              "glm-4",                "GLM-4 — Zhipu AI"),
            # MiniMax OpenAI-compatible
            ("minimax_openai",   "MiniMax-Text-01",      "MiniMax Text-01 — via OpenAI compat API"),
            # Ollama (local)
            ("ollama",           "llama3",               "LLaMA 3 — local"),
            ("ollama",           "llama3.1",             "LLaMA 3.1 — local"),
            ("ollama",           "codellama",            "Code LLaMA — local code assist"),
            ("ollama",           "mistral",              "Mistral — local"),
        ]

        for provider, model, desc in rows:
            table.add_row(provider, model, desc, key=f"{provider}:{model}")

        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = event.row_key.value
        provider, model = row_key.split(":", 1)
        if self._on_select:
            self._on_select(provider, model)
        self.dismiss()
