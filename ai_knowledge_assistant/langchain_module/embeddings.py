from langchain_huggingface import HuggingFaceEndpointEmbeddings
from dotenv import load_dotenv
from langchain_module.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

def get_embedding_model():
    logger.info("Loading embedding model: sentence-transformers/all-MiniLM-L6-v2")
    model = HuggingFaceEndpointEmbeddings(
        model="sentence-transformers/all-MiniLM-L6-v2",
        task="feature-extraction"
    )
    logger.info("Embedding model loaded successfully")
    return model