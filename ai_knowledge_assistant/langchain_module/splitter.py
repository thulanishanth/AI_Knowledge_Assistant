"""Module to handle text chunking and splitting for large documents."""
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def split_documents(documents):
    """Split the loaded documents into smaller overlapping text chunks."""
    logger.info("Splitting documents into chunks")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    chunks = splitter.split_documents(documents)
    logger.info("Documents split into %s chunks", len(chunks))
    return chunks
