# langchain_module/embeddings.py
from langchain_huggingface import HuggingFaceEndpointEmbeddings
import os
from dotenv import load_dotenv

load_dotenv()

def get_embedding_model():
    return HuggingFaceEndpointEmbeddings(
        model="sentence-transformers/all-MiniLM-L6-v2",
        task="feature-extraction"
    )