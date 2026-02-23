"""Module to handle the initialization of the data pipeline."""
from langchain_module.loader import load_documents
from langchain_module.splitter import split_documents
from langchain_module.embeddings import get_embedding_model
from langchain_module.vector_store import create_vector_store
from langchain_module.retriever import create_retriever

def setup_retriever(file_path: str):
    """Load, split, embed, and return a retriever for the given document."""
    documents = load_documents(file_path)
    chunks = split_documents(documents)
    embeddings_model = get_embedding_model()
    vector_store = create_vector_store(chunks, embeddings_model)
    return create_retriever(vector_store)
