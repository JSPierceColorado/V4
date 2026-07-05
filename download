import os


os.environ.setdefault("ADMIN_TOKEN", "test-token")

from fastapi.testclient import TestClient
from main import app, summarize_events


def test_chat_ui_loads() -> None:
    client = TestClient(app)
    response = client.get("/chat")
    assert response.status_code == 200
    assert "v4 Agentic Trader" in response.text
    assert "Attach CSV or PDF" in response.text


def test_research_status_endpoint_returns_idle_state() -> None:
    client = TestClient(app)
    response = client.get(
        "/research/status",
        headers={"X-Admin-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "research" in response.json()["reply"].lower()


def test_summarize_events_surfaces_operator_details() -> None:
    summary = summarize_events(
        [
            {
                "ts": "2026-07-04T18:16:26+00:00",
                "type": "agent_operator_journal",
                "payload": {
                    "summary": (
                        "Operator plan source: openai.\n"
                        "1. research: tested 48 variants on 5 symbols. "
                        "Deployed research_tp20_sl12_ms65_mh10_sb-5. "
                        "Validation return +0.58%, win rate +57.14%, trades 21."
                    )
                },
            }
        ]
    )

    assert "Recent actions (1)" in summary
    assert "operator journal" in summary
    assert "tested 48 variants on 5 symbols" in summary
