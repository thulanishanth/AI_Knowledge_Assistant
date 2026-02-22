# langchain_module/loader.py
"""Module to handle document loading from PDFs."""
from langchain_community.document_loaders import PyPDFLoader
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def load_documents(file_path):
    """Load a PDF file from the given path and return its documents."""
    logger.info(f"Loading documents from {file_path}")
    loader = PyPDFLoader(file_path)
    documents = loader.load()
    logger.info(f"Loaded {len(documents)} documents")
    return documents