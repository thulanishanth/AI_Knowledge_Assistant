"""Module to generate the FAISS vector database."""
from langchain_community.vectorstores import FAISS
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def create_vector_store(chunks, embedding_model):
    """Create a local FAISS vector store using the document chunks and embeddings."""
    logger.info("Creating vector store from %s chunks", len(chunks))
    vector_store = FAISS.from_documents(chunks, embedding_model)
    logger.info("Vector store created with %s vectors", vector_store.index.ntotal)
    return vector_store
