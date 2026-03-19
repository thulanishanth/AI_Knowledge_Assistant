# AI_Knowledge_Assistant/app/core/dependency_injection.py
"""Lightweight dependency injection container."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.infrastructure.repositories.chat_history_repository import ChatHistoryRepository
from app.infrastructure.repositories.schema_repository import SchemaRepository
from app.infrastructure.vector_store.chroma_store import ChromaStore
from app.core.session_manager import SessionContext
from app.memory.context_aggregator import ContextAggregator
from app.memory.memory_manager import MemoryManager
from app.memory.session_window_store import SessionWindowStore
from app.memory.summary_memory import SummaryMemory
from app.memory.vector_memory import VectorMemory
from app.services.embedding_service import EmbeddingService
from app.services.intent_service import IntentService
from app.services.prompt_builder import PromptBuilder
from app.services.query_orchestrator import QueryOrchestrator
from app.services.reranker_service import RerankerService
from app.services.response_formatter import ResponseFormatter
from app.services.schema_service import SchemaService
from app.services.sql_execution_service import SQLExecutionService
from app.services.sql_generation_service import SQLGenerationService
from app.security.sql_guard import SqlGuard


class SessionManager:  # pylint: disable=too-few-public-methods
    """Resolves request-level user/session identity with safe defaults."""

    def resolve(self, user_id: str | None = None, session_id: str | None = None) -> SessionContext:
        """Resolve incoming IDs and generate defaults when missing."""
        normalized_user = (user_id or "").strip() or "anonymous"
        normalized_session = (session_id or "").strip() or self._generate_session_id()
        return SessionContext(
            user_id=normalized_user,
            session_id=normalized_session,
            request_ts=datetime.now(timezone.utc),
        )

    @staticmethod
    def _generate_session_id() -> str:
        return f"sess_{uuid4().hex[:16]}"


class ServiceContainer:
    """Application-level service graph with shared singleton instances."""

    # pylint: disable=too-few-public-methods,too-many-instance-attributes
    def __init__(self) -> None:
        self.session_manager = SessionManager()
        self.chat_history_repository = ChatHistoryRepository()
        self.schema_repository = SchemaRepository()
        self.schema_service = SchemaService(self.schema_repository)
        self.intent_service = IntentService()
        self.sql_guard = SqlGuard()
        self.embedding_service = EmbeddingService()
        self.reranker_service = RerankerService()
        self.vector_store = ChromaStore()
        self.vector_memory = VectorMemory(
            vector_store=self.vector_store,
            embedding_service=self.embedding_service,
            reranker_service=self.reranker_service,
        )
        self.window_memory = SessionWindowStore()
        self.summary_memory = SummaryMemory()
        self.context_aggregator = ContextAggregator()
        self.prompt_builder = PromptBuilder()
        self.memory_manager = MemoryManager(
            vector_memory=self.vector_memory,
            window_memory=self.window_memory,
            summary_memory=self.summary_memory,
            context_aggregator=self.context_aggregator,
        )
        self.sql_generation_service = SQLGenerationService(
            schema_service=self.schema_service,
            sql_guard=self.sql_guard,
        )
        self.sql_execution_service = SQLExecutionService()
        self.response_formatter = ResponseFormatter()
        self.query_orchestrator = QueryOrchestrator(
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            schema_service=self.schema_service,
            intent_service=self.intent_service,
            sql_generation_service=self.sql_generation_service,
            sql_execution_service=self.sql_execution_service,
            response_formatter=self.response_formatter,
        )

    async def initialize(self) -> None:
        """Initialize services requiring async startup work."""
        await self.memory_manager.initialize_memory_manager()


container = ServiceContainer()
