from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def split_documents(documents):
    logger.info("Splitting documents into chunks")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    chunks = splitter.split_documents(documents)
    logger.info(f"Documents split into {len(chunks)} chunks")
    return chunks