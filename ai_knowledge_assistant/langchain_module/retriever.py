# langchain_module/retriever.py
from langchain_core.vectorstores import VectorStoreRetriever
def create_retriever(vector_store):
    retriever = vector_store.as_retriever(
        search_kwargs = {"k":4}
    )
    return retriever