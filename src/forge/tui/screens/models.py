"""
forge.tui.screens.models — Multi-provider model picker.

DataTable listing all available models grouped by provider.
Selecting a row switches the active model in AppState and returns.

Access: Ctrl+A from chat, or /model command.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Static

from forge.tui.state import AppState


class ModelPicker(Screen):
    """
    Model selection DataTable with provider grouping.

    Columns: Provider, Model, Description
    Keybindings:
      Escape  → close without changing
      Enter   → select and apply
      ↑↓      → navigate
    """

    BINDINGS = [
        ("escape", "app.pop_screen", "Close"),
    ]

    class ModelSelected(Message):
        def __init__(self, provider: str, model: str) -> None:
            self.provider = provider
            self.model = model
            super().__init__()

    CSS = """
    ModelPicker {
        layout: vertical;
    }

    #mp-title {
        height: 3;
        background: $primary;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }

    #mp-hint {
        height: 2;
        color: $text-muted;
        padding: 0 2;
    }
    """

    PROVIDERS = {
        "openai": {
            "label": "OpenAI",
            "models": [
                ("gpt-4o",               "GPT-4o — latest flagship"),
                ("gpt-4o-mini",          "GPT-4o mini — faster, cheaper"),
                ("gpt-4-turbo",          "GPT-4 Turbo — fast large context"),
                ("gpt-3.5-turbo",        "GPT-3.5 — legacy, very fast"),
            ],
        },
        "anthropic": {
            "label": "Anthropic",
            "models": [
                ("claude-opus-4-6",           "Claude Opus 4 — most capable"),
                ("claude-sonnet-4-6",         "Claude Sonnet 4 — best overall"),
                ("claude-3-5-sonnet-latest",  "Claude 3.5 Sonnet — balanced"),
                ("claude-3-5-haiku-latest",   "Claude 3.5 Haiku — fast"),
            ],
        },
        "deepseek": {
            "label": "DeepSeek",
            "models": [
                ("deepseek-chat",   "DeepSeek Chat — low cost"),
                ("deepseek-coder", "DeepSeek Coder — code specialized"),
            ],
        },
        "qwen": {
            "label": "Qwen / Alibaba",
            "models": [
                ("qwen-plus",  "Qwen Plus — balanced"),
                ("qwen-turbo", "Qwen Turbo — fast"),
            ],
        },
        "kimi": {
            "label": "Kimi / Moonshot",
            "models": [
                ("moonshot-v1-8k",  "Moonshot V1 8k context"),
                ("moonshot-v1-32k", "Moonshot V1 32k context"),
            ],
        },
        "glm": {
            "label": "GLM / Zhipu",
            "models": [
                ("glm-4", "GLM-4 — general purpose"),
            ],
        },
        "minimax_openai": {
            "label": "MiniMax (OpenAI compat)",
            "models": [
                ("MiniMax-Text-01", "MiniMax Text-01 — via OpenAI compat API"),
            ],
        },
        "ollama": {
            "label": "Ollama (local)",
            "models": [
                ("llama3",     "LLaMA 3 — local"),
                ("llama3.1",   "LLaMA 3.1 — local"),
                ("codellama",  "Code LLaMA — local code assist"),
                ("mistral",    "Mistral — local"),
                ("qwen2.5",    "Qwen 2.5 — local"),
            ],
        },
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.state = AppState.get()

    def compose(self) -> ComposeResult:
        yield Static(
            "Model Picker  (↑↓ navigate, Enter select, Esc close)",
            id="mp-title",
        )
        yield DataTable(id="mp-table")

    def on_mount(self) -> None:
        table = self.query_one("#mp-table", DataTable)
        table.add_columns("Provider", "Model", "Description")

        for provider_id, info in self.PROVIDERS.items():
            label = info["label"]
            for model, desc in info["models"]:
                table.add_row(
                    label, model, desc,
                    key=f"{provider_id}:{model}",
                )

        # Highlight current model
        cur = f"{self.state.current_provider}:{self.state.current_model}"
        table.scroll_to_row(table.row_count - 1)  # scroll to top
        table.move_cursor(row=0)

        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        provider, model = key.split(":", 1)
        self.state.update_model(provider, model)
        self.state.set_status(
            f"Model: {model} ({provider})", ttl=3,
        )
        self.dismiss()


# Alias for backward compatibility with any code expecting ModelPickerScreen
ModelPickerScreen = ModelPicker
