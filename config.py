import os
from dataclasses import dataclass
from typing import Tuple


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


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer; got {raw!r}") from exc


def env_symbols(name: str, default: str) -> Tuple[str, ...]:
    raw = os.getenv(name, default)
    symbols = []
    seen = set()
    for item in raw.replace("\n", ",").split(","):
        symbol = item.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return tuple(symbols)


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
    autonomy_enabled: bool
    autonomy_dry_run: bool
    autonomy_interval_seconds: int
    autonomy_symbols: Tuple[str, ...]
    autonomy_min_score: float
    autonomy_max_orders_per_cycle: int
    autonomy_max_positions: int
    autonomy_position_buying_power_pct: float
    autonomy_screen_symbols_per_cycle: int
    autonomy_research_enabled: bool
    autonomy_research_interval_seconds: int
    autonomy_research_symbols_per_run: int
    autonomy_research_max_variants: int
    autonomy_research_lookback_days: int
    autonomy_research_scout_symbols: int
    autonomy_research_validate_top_variants: int
    autonomy_research_require_profitable: bool
    autonomy_mutation_enabled: bool
    autonomy_mutation_variants: int
    autonomy_mutation_parent_count: int
    autonomy_ai_strategy_lab_enabled: bool
    autonomy_ai_strategy_ideas: int
    agent_operator_enabled: bool

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
        autonomy_enabled=env_bool("AUTONOMY_ENABLED", True),
        autonomy_dry_run=env_bool("AUTONOMY_DRY_RUN", False),
        autonomy_interval_seconds=env_int("AUTONOMY_INTERVAL_SECONDS", 600),
        autonomy_symbols=env_symbols("AUTONOMY_SYMBOLS", ""),
        autonomy_min_score=env_float("AUTONOMY_MIN_SCORE", 0.0),
        autonomy_max_orders_per_cycle=env_int("AUTONOMY_MAX_ORDERS_PER_CYCLE", 0),
        autonomy_max_positions=env_int("AUTONOMY_MAX_POSITIONS", 0),
        autonomy_position_buying_power_pct=env_float(
            "AUTONOMY_POSITION_BUYING_POWER_PCT", 0.02
        ),
        autonomy_screen_symbols_per_cycle=env_int(
            "AUTONOMY_SCREEN_SYMBOLS_PER_CYCLE", 100
        ),
        autonomy_research_enabled=env_bool("AUTONOMY_RESEARCH_ENABLED", True),
        autonomy_research_interval_seconds=env_int(
            "AUTONOMY_RESEARCH_INTERVAL_SECONDS", 21600
        ),
        autonomy_research_symbols_per_run=env_int(
            "AUTONOMY_RESEARCH_SYMBOLS_PER_RUN", 250
        ),
        autonomy_research_max_variants=env_int(
            "AUTONOMY_RESEARCH_MAX_VARIANTS", 1000
        ),
        autonomy_research_lookback_days=env_int(
            "AUTONOMY_RESEARCH_LOOKBACK_DAYS", 1095
        ),
        autonomy_research_scout_symbols=env_int(
            "AUTONOMY_RESEARCH_SCOUT_SYMBOLS", 60
        ),
        autonomy_research_validate_top_variants=env_int(
            "AUTONOMY_RESEARCH_VALIDATE_TOP_VARIANTS", 50
        ),
        autonomy_research_require_profitable=env_bool(
            "AUTONOMY_RESEARCH_REQUIRE_PROFITABLE", True
        ),
        autonomy_mutation_enabled=env_bool("AUTONOMY_MUTATION_ENABLED", True),
        autonomy_mutation_variants=env_int("AUTONOMY_MUTATION_VARIANTS", 160),
        autonomy_mutation_parent_count=env_int("AUTONOMY_MUTATION_PARENT_COUNT", 8),
        autonomy_ai_strategy_lab_enabled=env_bool(
            "AUTONOMY_AI_STRATEGY_LAB_ENABLED", True
        ),
        autonomy_ai_strategy_ideas=env_int("AUTONOMY_AI_STRATEGY_IDEAS", 48),
        agent_operator_enabled=env_bool("AGENT_OPERATOR_ENABLED", True),
    )
