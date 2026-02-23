# langchain_module/retriever.py
"""Module to configure the vector store retriever."""
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def create_retriever(vector_store):
    """Convert the vector store into a retriever fetching top 4 matches."""
    logger.info("Creating retriever from vector store")
    retriever = vector_store.as_retriever(search_kwargs={"k": 4})
    logger.info("Retriever created successfully")
    return retriever
