import pytest
from unittest.mock import AsyncMock, MagicMock
from app.memory.memory_manager import MemoryManager

async def test_get_context_for_llm():
    # 1. Create mocks for dependencies
    mock_vector = AsyncMock()
    mock_vector.retrieve_hybrid_context.return_value = [{"text": "vector memory text"}]
    
    mock_window = AsyncMock()
    mock_window.get_window.return_value = [{"role": "user", "content": "hello"}]
    
    mock_summary = AsyncMock()
    mock_summary.get_summary.return_value = "summary text"
    
    mock_aggregator = MagicMock()
    mock_aggregator.aggregate.return_value = "aggregated final context"

    # 2. Instantiate the manager with mocks
    manager = MemoryManager(
        vector_memory=mock_vector,
        window_memory=mock_window,
        summary_memory=mock_summary,
        context_aggregator=mock_aggregator
    )

    # 3. Call the method
    result = await manager.get_context_for_llm("user1", "session1", "test query")

    # 4. Assert behavior
    assert result["aggregated_context"] == "aggregated final context"
    mock_vector.retrieve_hybrid_context.assert_called_once_with(
        user_id="user1", session_id="session1", query="test query"
    )