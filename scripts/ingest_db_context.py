#app/scripts/ingest_db_context.py
import asyncio
import json
from pathlib import Path

from app.core.dependency_injection import container
from app.ingestion.knowledge_ingestion import KnowledgeIngestionService
from app.core.logging import get_logger

logger = get_logger(__name__)

async def ingest_database_context():
    """Extracts deep context (Semantics, Ontology, Rules, Constraints) and feeds them to Vector Memory."""
    
    # 1. Initialize dependencies (connects to DB and Vector Store)
    await container.initialize()
    ingestion_service = KnowledgeIngestionService(container.vector_memory)
    
    documents_to_embed = []
    
    # 2. Load the Business Rules from JSON
    context_file = Path(__file__).resolve().parent.parent / "app" / "data" / "business_context.json"
    
    if not context_file.exists():
        logger.error(f"Cannot find {context_file}. Please run dynamic_extractor.py first.")
        return

    logger.info("Loading enriched semantic context, ontology, and rules from JSON...")
    with open(context_file, "r") as f:
        business_context = json.load(f)
        
    dataset_name = business_context.get("dataset", "unknown_dataset")
    dialect = business_context.get("target_dialect", "sql")

    # Chunk 1: Semantic Layer
    for item in business_context.get("semantic_layer", []):
        documents_to_embed.append(f"[{dataset_name} SEMANTICS]: {item}")
        
    # Chunk 2: Ontology / Synonyms (Crucial for Natural Language Mapping)
    ontology = business_context.get("ontology", {})
    for col, synonyms in ontology.items():
        syn_str = ", ".join(synonyms)
        documents_to_embed.append(
            f"[{dataset_name} ONTOLOGY]: When the user mentions {syn_str}, they are referring to the '{col}' column."
        )

    # Chunk 3: Business Rules
    for item in business_context.get("business_rules", []):
        documents_to_embed.append(f"[{dataset_name} BUSINESS RULE]: {item}")
        
    # Chunk 4: Constraints
    for item in business_context.get("constraints", []):
        documents_to_embed.append(f"[{dataset_name} SYSTEM CONSTRAINT]: {item}")
        
    # Chunk 5: Examples (Dialect Aware)
    for item in business_context.get("examples", []):
        documents_to_embed.append(f"[{dataset_name} {dialect.upper()} EXAMPLE]:\n{item}")

    if not documents_to_embed:
        logger.warning("No context data found in JSON to embed.")
        return

    # 3. Ingest into Global Knowledge Memory
    logger.info(f"Embedding {len(documents_to_embed)} rich knowledge chunks into Vector Memory...")
    
    record_ids = await ingestion_service.ingest_documents(
        documents=documents_to_embed, 
        source=f"auto_extractor_{dataset_name}"
    )
    
    logger.info(f"✅ Successfully ingested {len(record_ids)} intelligent context rules into the AI Knowledge Base!")

if __name__ == "__main__":
    asyncio.run(ingest_database_context())