from agent import rule_parse


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
