import asyncio
import json
from pathlib import Path

from app.core.dependency_injection import container
from app.ingestion.knowledge_ingestion import KnowledgeIngestionService
from app.core.logging import get_logger

logger = get_logger(__name__)

async def ingest_database_context():
    """Extracts Schema, Semantics, Constraints, and Examples and feeds them to ChromaDB."""
    
    # 1. Initialize dependencies (connects to DB and Vector Store)
    await container.initialize()
    ingestion_service = KnowledgeIngestionService(container.vector_memory)
    
    documents_to_embed = []
    
    # 2. Load the Business Rules from JSON
    context_file = Path(__file__).resolve().parent.parent / "app" / "data" / "business_context.json"
    
    if not context_file.exists():
        logger.error(f"Cannot find {context_file}. Please create it first.")
        return

    logger.info("Loading semantic layer and constraints from JSON...")
    with open(context_file, "r") as f:
        business_context = json.load(f)
        
    # Chunk 1: Semantics
    for item in business_context.get("semantic_layer", []):
        documents_to_embed.append(f"SEMANTIC DEFINITION:\n{item}")
        
    # Chunk 2: Constraints
    for item in business_context.get("constraints", []):
        documents_to_embed.append(f"BUSINESS CONSTRAINT:\n{item}")
        
    # Chunk 3: Examples
    for item in business_context.get("examples", []):
        documents_to_embed.append(f"SQL EXAMPLE:\n{item}")

    # 3. Ingest into Global Knowledge Memory
    logger.info(f"Embedding {len(documents_to_embed)} knowledge chunks into Vector Memory...")
    
    record_ids = await ingestion_service.ingest_documents(
        documents=documents_to_embed, 
        source="db_context_script"
    )
    
    logger.info(f"Successfully ingested {len(record_ids)} context rules into the AI Knowledge base!")

if __name__ == "__main__":
    asyncio.run(ingest_database_context())