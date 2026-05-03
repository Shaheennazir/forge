from dataclasses import dataclass

@dataclass
class AppContext:
    current_session: str = None
    current_model: str = None
    providers: list = None
