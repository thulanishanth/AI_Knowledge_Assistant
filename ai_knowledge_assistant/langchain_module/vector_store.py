from langchain_community.vectorstores import FAISS
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def create_vector_store(chunks, embedding_model):
    logger.info(f"Creating vector store from {len(chunks)} chunks")
    vector_store = FAISS.from_documents(chunks, embedding_model)
    logger.info(f"Vector store created with {vector_store.index.ntotal} vectors")
    return vector_store