from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def create_rag_chain(llm, retriever, prompt):
    logger.info("Creating RAG chain")

    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)

    rag_chain = (
        {
            "context": retriever | format_docs,
            "question": RunnablePassthrough()
        }
        | prompt
        | llm
        | StrOutputParser()
    )
    logger.info("RAG chain created successfully")
    return rag_chain