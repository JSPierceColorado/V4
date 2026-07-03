import os


os.environ.setdefault("ADMIN_TOKEN", "test-token")

from fastapi.testclient import TestClient
from main import app


def test_chat_ui_loads() -> None:
    client = TestClient(app)
    response = client.get("/chat")
    assert response.status_code == 200
    assert "v4 Agentic Trader" in response.text
    assert "Upload CSV or PDF" in response.text
