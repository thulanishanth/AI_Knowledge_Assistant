# tests/unit/test_query_service.py
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import app.services.query_service as qs


@pytest.fixture(autouse=True)
def mock_container(monkeypatch):
    """Mock dependency injection container used by query_service."""
    mock_session_ctx = SimpleNamespace(user_id="u1", session_id="s1")

    mock_session_manager = MagicMock()
    mock_session_manager.resolve.return_value = mock_session_ctx

    mock_memory_manager = MagicMock()
    mock_memory_manager.get_context_for_llm = AsyncMock(
        return_value={
            "vector_results": [],
            "window_messages": [],
            "summary": "",
            "rag_context": "",
            "aggregated_context": "test context",
        }
    )
    mock_memory_manager.update_memory_pipeline = AsyncMock()

    mock_prompt_builder = MagicMock()
    mock_prompt_builder.build_memory_prompt.return_value = "general prompt"

    mock_container = SimpleNamespace(
        session_manager=mock_session_manager,
        memory_manager=mock_memory_manager,
        prompt_builder=mock_prompt_builder,
    )

    monkeypatch.setattr(qs, "container", mock_container)


@pytest.mark.asyncio
async def test_empty_question():
    """Should raise error for empty input."""
    with pytest.raises(ValueError):
        await qs.handle_query("")


@pytest.mark.asyncio
async def test_greeting_path():
    """Greeting should bypass pipeline."""
    answer, confidence, session = await qs.handle_query("hello")

    assert "help you today" in answer.lower()
    assert confidence == 1.0
    assert session == "s1"


@pytest.mark.asyncio
async def test_general_llm_path(monkeypatch):
    """Non-SQL intent should trigger general LLM response."""
    monkeypatch.setattr(qs, "classify_intent", lambda x: "general")
    monkeypatch.setattr(qs, "apply_rules", lambda q, i: q)
    monkeypatch.setattr(qs, "call_llm", lambda prompt: "AI response")
    monkeypatch.setattr(qs, "check_confidence", lambda r, c: 0.8)

    answer, confidence, _ = await qs.handle_query("Explain AI")

    assert "ai response" in answer.lower()
    assert confidence == 0.8


@pytest.mark.asyncio
async def test_sql_success_flow(monkeypatch):
    """SQL path should validate, execute query, and format result."""
    monkeypatch.setattr(qs, "classify_intent", lambda x: "sql")
    monkeypatch.setattr(qs, "apply_rules", lambda q, i: q)
    monkeypatch.setattr(qs, "build_prompt", lambda q, c, t: "sql prompt")
    monkeypatch.setattr(qs, "call_llm", lambda p: "SELECT * FROM users;")
    monkeypatch.setattr(qs, "validate_sql", lambda q: True)
    monkeypatch.setattr(qs, "execute_safe_query", lambda q: [("Alice",)])
    monkeypatch.setattr(qs, "format_sql_results", lambda r, q: "User list")
    monkeypatch.setattr(qs, "format_answer", lambda x: x)

    answer, confidence, _ = await qs.handle_query("show users")

    assert answer == "User list"
    assert confidence == 1.0


@pytest.mark.asyncio
async def test_sql_invalid_fallback(monkeypatch):
    """Invalid SQL should fallback to general LLM answer."""
    monkeypatch.setattr(qs, "classify_intent", lambda x: "sql")
    monkeypatch.setattr(qs, "apply_rules", lambda q, i: q)
    monkeypatch.setattr(qs, "build_prompt", lambda q, c, t: "sql prompt")
    monkeypatch.setattr(qs, "call_llm", lambda p: "INVALID SQL")
    monkeypatch.setattr(qs, "validate_sql", lambda q: False)
    monkeypatch.setattr(qs, "check_confidence", lambda r, c: 0.5)
    monkeypatch.setattr(qs, "format_answer", lambda x: x)

    answer, confidence, _ = await qs.handle_query("show users")

    assert answer
    assert confidence == 0.5


@pytest.mark.asyncio
async def test_llm_runtime_error(monkeypatch):
    """If LLM fails, system should return backpressure message."""
    monkeypatch.setattr(qs, "classify_intent", lambda x: "general")
    monkeypatch.setattr(qs, "apply_rules", lambda q, i: q)

    def raise_error(_):
        raise RuntimeError("LLM failed")

    monkeypatch.setattr(qs, "call_llm", raise_error)

    answer, confidence, _ = await qs.handle_query("tell me something")

    assert qs.LLM_BACKPRESSURE_MESSAGE in answer
    assert confidence == 0.0
