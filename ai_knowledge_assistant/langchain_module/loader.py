# langchain_module/loader.py
"""Module to handle document loading from PDFs."""
from langchain_community.document_loaders import PyPDFLoader

def load_documents(file_path):
    """Load a PDF file from the given path and return its documents."""
    loader = PyPDFLoader(file_path)
    documents = loader.load()
    return documents