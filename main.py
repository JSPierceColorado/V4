import os
from dataclasses import dataclass


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number; got {raw!r}") from exc


def normalize_openai_model(raw: str) -> str:
    model = raw.strip()
    if not model:
        return "gpt-5.2"
    aliases = {
        "5": "gpt-5",
        "5.1": "gpt-5.1",
        "5.2": "gpt-5.2",
        "mini": "gpt-5-mini",
        "nano": "gpt-5-nano",
    }
    return aliases.get(model.lower(), model)


@dataclass(frozen=True)
class Settings:
    app_name: str
    admin_token: str
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_paper: bool
    alpaca_trading_base_url: str
    alpaca_data_base_url: str
    alpaca_data_feed: str
    openai_api_key: str
    openai_model: str
    data_dir: str
    default_order_qty: float
    default_time_in_force: str
    extended_hours: bool
    max_upload_bytes: int

    @property
    def alpaca_ready(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_api_key)


def load_settings() -> Settings:
    paper = env_bool("ALPACA_PAPER", True)
    default_base_url = (
        "https://paper-api.alpaca.markets"
        if paper
        else "https://api.alpaca.markets"
    )
    return Settings(
        app_name=os.getenv("APP_NAME", "v4-agentic-trader").strip()
        or "v4-agentic-trader",
        admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
        alpaca_api_key=(
            os.getenv("ALPACA_API_KEY")
            or os.getenv("APCA_API_KEY_ID")
            or ""
        ).strip(),
        alpaca_secret_key=(
            os.getenv("ALPACA_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
            or ""
        ).strip(),
        alpaca_paper=paper,
        alpaca_trading_base_url=os.getenv(
            "ALPACA_TRADING_BASE_URL", default_base_url
        ).strip(),
        alpaca_data_base_url=os.getenv(
            "ALPACA_DATA_BASE_URL", "https://data.alpaca.markets"
        ).strip(),
        alpaca_data_feed=os.getenv("ALPACA_DATA_FEED", "iex").strip().lower()
        or "iex",
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_model=normalize_openai_model(os.getenv("OPENAI_MODEL", "gpt-5.2")),
        data_dir=os.getenv("DATA_DIR", "./data").strip() or "./data",
        default_order_qty=env_float("DEFAULT_ORDER_QTY", 1.0),
        default_time_in_force=os.getenv("DEFAULT_TIME_IN_FORCE", "day").strip().lower()
        or "day",
        extended_hours=env_bool("EXTENDED_HOURS", False),
        max_upload_bytes=int(env_float("MAX_UPLOAD_MB", 10) * 1024 * 1024),
    )
