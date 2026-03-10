from app.services.llm_client import call_llm


def test_llm_call(monkeypatch):

    def mock_router(prompt, max_tokens):
        return "SELECT * FROM hotel_reservations"

    monkeypatch.setattr(
        "app.services.llm_client._call_llm_router",
        mock_router
    )

    result = call_llm("generate sql")

    assert "SELECT" in result