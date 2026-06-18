import pytest
from app.core.dependency_injection import container

async def test_container_initialization(monkeypatch):
    # Mock the underlying ChromaDB initialize to prevent network calls
    async def mock_chroma_init():
        pass
    
    monkeypatch.setattr(container.vector_store, "initialize", mock_chroma_init)
    
    await container.initialize()
    
    assert container.memory_manager is not None
    assert container.prompt_builder is not None