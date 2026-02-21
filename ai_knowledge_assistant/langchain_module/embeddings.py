# langchain_module/embeddings.py
"""Module to initialize the document embedding model."""
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from dotenv import load_dotenv

load_dotenv()

def get_embedding_model():
    """Load and return the HuggingFace sentence transformer embedding model."""
    return HuggingFaceEndpointEmbeddings(
        model="sentence-transformers/all-MiniLM-L6-v2",
        task="feature-extraction"
    )