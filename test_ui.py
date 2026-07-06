from agent_operator import (
    build_operator_context,
    fallback_plan,
    journal_operator_cycle,
    model_plan,
    summarize_tool_result,
)
from config import load_settings
from storage import load_events


def _state(is_open: bool = False):
    return {
        "clock": {"is_open": is_open, "next_open": "2026-07-06T13:30:00Z"},
        "account": {
            "status": "ACTIVE",
            "cash": "1000",
            "buying_power": "2000",
            "equity": "1200",
        },
        "positions": [],
        "open_orders": [],
    }


def test_operator_context_includes_tools_and_recent_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = load_settings()
    context = build_operator_context(
        settings,
        state=_state(),
        autonomy_status={"running": True},
    )

    assert context["operator_policy"]["paper_account_only"] is True
    assert "autonomy_cycle" in context["operator_policy"]["allowed_tools"]
    assert "market_brief" in context["operator_policy"]["allowed_tools"]
    assert context["state"]["account"]["buying_power"] == "2000"


def test_model_plan_falls_back_without_openai(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = load_settings()
    context = build_operator_context(
        settings,
        state=_state(is_open=True),
        autonomy_status={"running": True, "research_enabled": False},
    )

    plan = model_plan(settings, context)

    assert plan["source"] == "fallback_no_openai"
    assert plan["actions"][0]["tool"] == "autonomy_cycle"


def test_operator_journal_writes_recent_event(tmp_path) -> None:
    journal = journal_operator_cycle(
        str(tmp_path),
        plan={
            "source": "test",
            "rationale": "Check account.",
            "actions": [{"tool": "state", "args": {}, "reason": "inspect"}],
        },
        results=[{"tool": "state", "ok": True, "reply": "State loaded."}],
    )

    events = load_events(str(tmp_path), limit=5)

    assert journal["ok"] is True
    assert "Check account" in journal["summary"]
    assert events[-1]["type"] == "agent_operator_journal"


def test_operator_summary_shows_research_skipped_not_due(tmp_path) -> None:
    journal = journal_operator_cycle(
        str(tmp_path),
        plan={
            "source": "openai",
            "rationale": "Market is closed; research if useful and check clock.",
            "actions": [],
        },
        results=[
            {
                "tool": "research",
                "reason": "not_due",
                "ok": True,
                "skipped": True,
                "last_periodic_research_at": "2026-07-04T17:47:37+00:00",
            },
            {
                "tool": "clock",
                "ok": True,
                "reply": "Clock loaded.",
                "clock": {
                    "is_open": False,
                    "next_open": "2026-07-06T09:30:00-04:00",
                    "next_close": "2026-07-06T16:00:00-04:00",
                },
            },
        ],
    )

    assert "research: skipped - not due yet" in journal["summary"]
    assert "Last periodic research: 2026-07-04T17:47:37+00:00" in journal["summary"]
    assert "clock: market closed. Next open: 2026-07-06T09:30:00-04:00" in journal["summary"]


def test_operator_summary_shows_research_backtest_result() -> None:
    summary = summarize_tool_result(
        {
            "tool": "research",
            "ok": True,
            "research": {
                "variants_tested": 48,
                "bars_symbols": 25,
                "best_strategy_id": "research_tp40_sl25_ms55_mh20_sb0",
                "best": {
                    "validation": {
                        "total_return_pct": 0.032,
                        "win_rate": 0.58,
                        "trades": 14,
                    }
                },
            },
        }
    )

    assert "scouted 48 variants and validated 48 finalists on 25 symbols" in summary
    assert "Validation return +3.20%" in summary
    assert "win rate +58.00%" in summary
