"""Streamlit web interface for the AI Knowledge Assistant."""
import streamlit as st
from langchain_module.logger import get_logger
from langchain_module.pipeline import setup_retriever
from langchain_module.llm import load_llm_model
from langchain_module.prompt import get_prompt
from langchain_module.rag_chain import create_rag_chain

logger = get_logger(__name__)

# Configure the visual styling of the browser tab
st.set_page_config(page_title="AI Java Tutor", page_icon="☕", layout="centered")

@st.cache_resource(show_spinner="Loading AI Models and reading documentation...")
def initialize_rag_pipeline():
    """Load all models and documents once and cache them in memory."""
    try:
        retriever = setup_retriever("data/Top 50 Java Interview Questions For Freshers.pdf")
        llm = load_llm_model()
        prompt = get_prompt()
        return create_rag_chain(llm, retriever, prompt)
    except (FileNotFoundError, ValueError, ConnectionError, RuntimeError) as e:
        logger.error("Pipeline initialization failed: %s", e)
        st.error(f"Error loading models: {e}")
        return None

# Build the UI Header
st.title("☕ Java Interview Assistant")
st.markdown("Ask me anything about Java!.")
# Initialize the AI pipeline
rag_chain = initialize_rag_pipeline()

# Initialize Chat History in Session State
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display previous chat messages on the screen
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# User Input Box
if user_input := st.chat_input("Ask a Java question... (e.g., 'What is a constructor?')"):
    # 1. Add user message to state and display it
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 2. Generate AI response if the pipeline loaded successfully
    if rag_chain:
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    # Query the RAG chain
                    answer = rag_chain.invoke(user_input)
                    st.markdown(answer)
                    # Save AI response to history
                    st.session_state.messages.append({"role": "assistant", "content": answer})
                except (ConnectionError, TimeoutError, RuntimeError) as e:
                    st.error("Failed to generate a response from the API.")
                    logger.error("Generation error: %s", e)
