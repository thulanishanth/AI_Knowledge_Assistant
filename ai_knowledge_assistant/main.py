# main.py
"""Main entry point for the AI Knowledge Assistant."""
from langchain_module.logger import get_logger
from langchain_module.loader import load_documents
from langchain_module.splitter import split_documents
from langchain_module.embeddings import get_embedding_model
from langchain_module.vector_store import create_vector_store
from langchain_module.retriever import create_retriever
from langchain_module.llm import load_llm_model
from langchain_module.prompt import get_prompt
from langchain_module.rag_chain import create_rag_chain

logger = get_logger(__name__)

try:
    # Load documents
    documents = load_documents("data/Top 50 Java Interview Questions For Freshers.pdf")
    
    # Split documents
    chunks = split_documents(documents)
    
    # Embeddings
    embeddings_model = get_embedding_model()
    
    # Vector Store
    vector_store = create_vector_store(chunks, embeddings_model)
    
    # Retriever
    retriever = create_retriever(vector_store)

    # Debug sample retrieval
    sample_docs = retriever.invoke("What is constructor?")
    logger.info(f"Sample context retrieved ({len(sample_docs)} docs)")

    # LLM
    llm = load_llm_model()
    
    # Prompt
    prompt = get_prompt()
    
    # RAG Chain
    rag_chain = create_rag_chain(llm, retriever, prompt)
    
    # User Interaction Loop
    while True:
        question = input("\nAsk your question: ")
        if question.lower() == "exit":
            logger.info("User exited the session")
            break
        answer = rag_chain.invoke(question)
        logger.info(f"Question: {question} | Answer: {answer}")
        print(answer)

except Exception as e:
    logger.exception(f"An error occurred: {e}")