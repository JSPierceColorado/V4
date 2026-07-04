from config import normalize_openai_model
from v4_brain import friendly_openai_error, rule_parse


def test_rule_parse_limit_buy() -> None:
    parsed = rule_parse("buy 1 share of AAPL with a limit order at 190")
    assert parsed["action"] == "place_order"
    assert parsed["args"]["symbol"] == "AAPL"
    assert parsed["args"]["side"] == "buy"
    assert parsed["args"]["qty"] == 1.0
    assert parsed["args"]["order_type"] == "limit"
    assert parsed["args"]["limit_price"] == 190.0


def test_rule_parse_cancel_all() -> None:
    parsed = rule_parse("cancel all open orders")
    assert parsed["action"] == "cancel_all_orders"


def test_rule_parse_close_all() -> None:
    parsed = rule_parse("close all positions")
    assert parsed["action"] == "close_all_positions"


def test_rule_parse_metrics() -> None:
    parsed = rule_parse("show me graphs and performance metrics")
    assert parsed["action"] == "metrics"


def test_rule_parse_screen() -> None:
    parsed = rule_parse("screen the market")
    assert parsed["action"] == "screen"


def test_rule_parse_autonomy_cycle() -> None:
    parsed = rule_parse("run one autonomous cycle")
    assert parsed["action"] == "autonomy_cycle"


def test_rule_parse_research() -> None:
    parsed = rule_parse("research and backtest new strategy variants")
    assert parsed["action"] == "research"


def test_rule_parse_research_status() -> None:
    parsed = rule_parse("research status")
    assert parsed["action"] == "research_status"


def test_rule_parse_market_clock() -> None:
    parsed = rule_parse("is the market open?")
    assert parsed["action"] == "clock"


def test_rule_parse_recent_actions() -> None:
    parsed = rule_parse("show recent actions")
    assert parsed["action"] == "events"


def test_rule_parse_operator_report() -> None:
    parsed = rule_parse("operator report")
    assert parsed["action"] == "events"


def test_rule_parse_agent_operator() -> None:
    parsed = rule_parse("run operator")
    assert parsed["action"] == "agent_cycle"


def test_normalize_openai_model_aliases() -> None:
    assert normalize_openai_model("5.1") == "gpt-5.1"
    assert normalize_openai_model("5.2") == "gpt-5.2"
    assert normalize_openai_model("5.4") == "gpt-5.4"
    assert normalize_openai_model("5.5") == "gpt-5.5"
    assert normalize_openai_model("") == "gpt-5.5"
    assert normalize_openai_model("mini") == "gpt-5-mini"


def test_friendly_quota_error() -> None:
    message = friendly_openai_error(Exception("code='insufficient_quota'"))
    assert "quota" in message.lower()
    assert "rule-based" in message
