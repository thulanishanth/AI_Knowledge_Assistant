"""Main entry point for the AI Knowledge Assistant."""
from langchain_module.logger import get_logger
from langchain_module.pipeline import setup_retriever
from langchain_module.llm import load_llm_model
from langchain_module.prompt import get_prompt
from langchain_module.rag_chain import create_rag_chain

logger = get_logger(__name__)

try:
    # Set up the retriever pipeline using the single source of truth
    retriever = setup_retriever("data/Top 50 Java Interview Questions For Freshers.pdf")

    # Debug sample retrieval
    sample_docs = retriever.invoke("What is constructor?")
    logger.info("Sample context retrieved (%s docs)", len(sample_docs))

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
        logger.info("Question: %s | Answer: %s", question, answer)
        print(answer)

except Exception as e:  # pylint: disable=broad-exception-caught
    logger.exception("An error occurred: %s", e)
