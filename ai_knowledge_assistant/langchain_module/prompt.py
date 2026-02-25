"""Module to define the system and chat prompts for the AI."""
from langchain_core.prompts import ChatPromptTemplate
from langchain_module.logger import get_logger

logger = get_logger(__name__)

def get_prompt():
    """Create and return the formatted chat prompt template."""
    logger.info("Creating chat prompt template")
    system_prompt = """
    You are an expert Java instructor.

    Use ONLY the provided context to answer the question.

    Rules:
    1. Give a clear and complete explanation.
    2. If context contains definition, explain in simple words.
    3. If answer is missing, say "I don't know".
    4. Answer in 3-5 sentences maximum.

    Context:
    {context}
    """
    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{question}")
    ])
    logger.info("Prompt template created successfully")
    return prompt_template
