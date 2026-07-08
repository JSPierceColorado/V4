from datetime import datetime, timezone

from market_clock import get_market_clock, is_market_open, normalize_market_clock


class FakeAlpacaCalendar:
    def calendar(self, *, start, end):
        return [{"date": start, "open": "09:30", "close": "16:00"}]


def test_string_is_open_is_coerced_true() -> None:
    clock = normalize_market_clock(
        FakeAlpacaCalendar(),
        {"is_open": "true"},
        now=datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc),
    )

    assert clock["is_open"] is True
    assert is_market_open(clock) is True


def test_calendar_overrides_false_clock_during_regular_session() -> None:
    clock = normalize_market_clock(
        FakeAlpacaCalendar(),
        {"is_open": False, "timestamp": "2026-07-08T15:00:00Z"},
        now=datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc),
    )

    assert clock["is_open"] is True
    assert clock["alpaca_is_open"] is False
    assert clock["clock_source"] == "alpaca_calendar_fallback"
    assert clock["clock_fallback_reason"] == "alpaca_clock_reported_closed_inside_regular_session"


def test_calendar_keeps_market_closed_outside_regular_session() -> None:
    clock = normalize_market_clock(
        FakeAlpacaCalendar(),
        {"is_open": False, "timestamp": "2026-07-08T22:00:00Z"},
        now=datetime(2026, 7, 8, 22, 0, tzinfo=timezone.utc),
    )

    assert clock["is_open"] is False
    assert is_market_open(clock) is False


def test_get_market_clock_uses_state_clock() -> None:
    class FakeAlpaca(FakeAlpacaCalendar):
        def clock(self):
            raise AssertionError("state clock should be reused")

    clock = get_market_clock(
        FakeAlpaca(),
        {"clock": {"is_open": "open"}},
    )

    assert clock["is_open"] is True


def test_operator_cycle_uses_calendar_fallback_before_planning(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AUTONOMY_RESEARCH_ENABLED", "false")

    from autonomy import AutonomyEngine
    from config import load_settings

    class FakeAlpaca(FakeAlpacaCalendar):
        def state(self):
            return {
                "clock": {"is_open": False, "timestamp": "2026-07-08T15:00:00Z"},
                "account": {"buying_power": "1000"},
                "positions": [],
                "open_orders": [],
            }

    settings = load_settings()
    engine = AutonomyEngine(settings)
    monkeypatch.setattr(
        engine,
        "run_cycle",
        lambda alpaca: {"ok": True, "summary": "normalized open clock ran autonomy cycle"},
    )
    monkeypatch.setattr(
        "market_clock.datetime",
        type(
            "FrozenDateTime",
            (),
            {
                "now": staticmethod(lambda tz=None: datetime(2026, 7, 8, 15, 0, tzinfo=timezone.utc)),
                "fromisoformat": staticmethod(datetime.fromisoformat),
                "combine": staticmethod(datetime.combine),
            },
        ),
    )

    result = engine.run_operator_cycle(FakeAlpaca())

    assert result["operator"]["plan"]["actions"][0]["tool"] == "autonomy_cycle"
    assert "normalized open clock ran autonomy cycle" in result["summary"]


def test_operator_guardrail_replaces_market_open_wait_with_autonomy() -> None:
    from agent_operator import enforce_autonomy_guardrails

    context = {"state": {"clock": {"is_open": True}}}
    plan = {
        "source": "openai",
        "rationale": "Flat book, review then wait.",
        "actions": [
            {"tool": "review_positions", "args": {}, "reason": "check theses"},
            {"tool": "wait", "args": {}, "reason": "preserve capital"},
        ],
    }

    patched = enforce_autonomy_guardrails(context, plan)

    assert [action["tool"] for action in patched["actions"]] == [
        "review_positions",
        "autonomy_cycle",
    ]
    assert "market_open_requires_autonomy_cycle_before_wait" in patched["guardrails"]
    assert "Autonomy guardrail" in patched["rationale"]


def test_operator_guardrail_leaves_market_closed_wait_alone() -> None:
    from agent_operator import enforce_autonomy_guardrails

    plan = {"actions": [{"tool": "wait", "args": {}, "reason": "market closed"}]}
    patched = enforce_autonomy_guardrails({"state": {"clock": {"is_open": False}}}, plan)

    assert patched["actions"] == plan["actions"]
    assert "guardrails" not in patched


def test_operator_cycle_forces_autonomy_before_wait_plan(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("AUTONOMY_RESEARCH_ENABLED", "false")

    from autonomy import AutonomyEngine
    from config import load_settings

    class FakeAlpaca(FakeAlpacaCalendar):
        def state(self):
            return {
                "clock": {"is_open": True, "timestamp": "2026-07-08T15:00:00Z"},
                "account": {"buying_power": "1000"},
                "positions": [],
                "open_orders": [],
            }

    settings = load_settings()
    engine = AutonomyEngine(settings)
    monkeypatch.setattr(
        "autonomy.model_plan",
        lambda settings, context: {
            "source": "openai",
            "rationale": "Flat book, review then wait.",
            "actions": [
                {"tool": "review_positions", "args": {}, "reason": "check theses"},
                {"tool": "wait", "args": {}, "reason": "preserve capital"},
            ],
        },
    )
    monkeypatch.setattr(
        engine,
        "run_cycle",
        lambda alpaca: {"ok": True, "summary": "guardrail autonomy cycle ran"},
    )

    result = engine.run_operator_cycle(FakeAlpaca())

    assert [action["tool"] for action in result["operator"]["plan"]["actions"]] == [
        "review_positions",
        "autonomy_cycle",
    ]
    assert "guardrail autonomy cycle ran" in result["summary"]
