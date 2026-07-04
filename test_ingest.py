from agent_operator import (
    build_operator_context,
    fallback_plan,
    journal_operator_cycle,
    model_plan,
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
