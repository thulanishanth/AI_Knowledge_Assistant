# langchain_module/prompt.py
# langchain_module/prompt.py
from langchain_core.prompts import ChatPromptTemplate

def get_prompt():
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
    
    return ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{question}")
    ])