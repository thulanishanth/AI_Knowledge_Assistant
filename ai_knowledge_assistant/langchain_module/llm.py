from langchain_huggingface import HuggingFaceEndpoint, ChatHuggingFace
from dotenv import load_dotenv
from langchain_module.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

def load_llm_model():
    logger.info("Loading LLM model: Qwen/Qwen2.5-7B-Instruct")
    llm = HuggingFaceEndpoint(
        repo_id="Qwen/Qwen2.5-7B-Instruct",
        max_new_tokens=250,
        temperature=0.1
    )
    chat_model = ChatHuggingFace(llm=llm)
    logger.info("LLM model loaded and wrapped for chat")
    return chat_model