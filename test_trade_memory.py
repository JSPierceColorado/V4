from trade_memory import (
    build_entry_thesis,
    build_exit_review,
    find_open_thesis,
    open_trade_theses,
    record_entry_thesis,
    record_exit_review,
)


def test_trade_thesis_ledger_reconstructs_open_and_closed_theses(tmp_path) -> None:
    thesis = build_entry_thesis(
        str(tmp_path),
        symbol="CAE",
        strategy={
            "id": "web_1_liquid_strength",
            "label": "Liquid strength",
            "source": "web_strategy_lab",
            "family": "web_dsl",
            "take_profit_pct": 0.04,
            "stop_loss_pct": -0.02,
            "max_hold_days": 10,
        },
        candidate={
            "symbol": "CAE",
            "score": 82,
            "ret_20d_pct": 4.2,
            "volume_ratio": 1.4,
            "dollar_vol_20d_m": 12.5,
        },
        adjusted_score=82,
        notional=40,
        order={"id": "paper-order-1"},
        market_clock={"is_open": True},
        account={"equity": "2000", "cash": "2000", "buying_power": "8000"},
        research_state={
            "active_strategy_id": "web_1_liquid_strength",
            "last_research": {
                "researched_at": "2026-07-06T00:00:00+00:00",
                "web_variants_tested": 3,
                "best": {"validation": {"total_return_pct": 0.04}},
            },
        },
        source="autonomy_cycle",
    )
    record_entry_thesis(str(tmp_path), thesis)

    assert find_open_thesis(str(tmp_path), "CAE")["thesis_id"] == thesis["thesis_id"]
    assert len(open_trade_theses(str(tmp_path))) == 1

    review = build_exit_review(
        str(tmp_path),
        symbol="CAE",
        reason="take_profit",
        plpc=0.05,
        position={"symbol": "CAE", "qty": "1"},
        order={"id": "sell-order-1"},
        exit_record={"symbol": "CAE", "reason": "take_profit"},
    )
    record_exit_review(str(tmp_path), review)

    assert open_trade_theses(str(tmp_path)) == []
    assert review["entry_thesis_summary"]["entry_reason"]
