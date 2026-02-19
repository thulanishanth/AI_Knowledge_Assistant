# langchain_module/llm.py
from langchain_huggingface import HuggingFaceEndpoint, ChatHuggingFace
import os
from dotenv import load_dotenv

load_dotenv()

def load_llm_model():
    llm = HuggingFaceEndpoint(
        repo_id="Qwen/Qwen2.5-7B-Instruct", # Updated to a currently active and accurate model
        max_new_tokens=250,
        temperature=0.1
    )
    # Wrap it so LangChain formats the requests as a chat conversation
    chat_model = ChatHuggingFace(llm=llm)
    return chat_model