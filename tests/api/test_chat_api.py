import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_chat_endpoint_success(monkeypatch):
    # 1. Create an async mock for handle_query
    async def mock_handle_query(*args, **kwargs):
        return ("Mocked Answer", 0.99, "sess_123")

    # 2. Patch it exactly where it is used in the router
    monkeypatch.setattr("app.api.chat.handle_query", mock_handle_query)

    # 3. Patch the sync background tasks (save_chat_message) so it doesn't try to hit MySQL
    monkeypatch.setattr("app.api.chat.save_chat_message", lambda *args, **kwargs: None)

    # 4. Execute the request
    response = client.post(
        "/api/chat/",
        json={"question": "What is the status of my order?"}
    )

    # 5. Assert the response
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Mocked Answer"
    assert data["confidence"] == 0.99
    assert data["session_id"] == "sess_123"