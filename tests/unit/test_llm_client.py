import pytest

import app.services.llm_client as llm_client


def test_llm_call(monkeypatch):

    def mock_router(prompt, max_tokens):
        return "SELECT * FROM hotel_reservations"

    monkeypatch.setattr(
        "app.services.llm_client._call_llm_router",
        mock_router
    )

    result = llm_client.call_llm("generate sql")

    assert "SELECT" in result


def test_non_retryable_credit_error_is_humanized(monkeypatch):
    def fail_router(*args, **kwargs):
        raise RuntimeError("402 Payment Required: included credits exhausted")

    def fail_legacy(*args, **kwargs):
        raise RuntimeError("402 Payment Required: included credits exhausted")

    monkeypatch.setattr("app.services.llm_client._call_llm_router", fail_router)
    monkeypatch.setattr("app.services.llm_client._call_llm_legacy", fail_legacy)

    with pytest.raises(RuntimeError, match="exhausted"):
        llm_client.call_llm("generate sql", max_retries=2)
