# main.py
"""Main entry point for the AI Knowledge Assistant."""
from langchain_module.loader import load_documents
from langchain_module.splitter import split_documents
from langchain_module.embeddings import get_embedding_model
from langchain_module.vector_store import create_vector_store
from langchain_module.retriever import create_retriever
from langchain_module.llm import load_llm_model
from langchain_module.prompt import get_prompt
from langchain_module.rag_chain import create_rag_chain


# Load documents
documents = load_documents("data/Top 50 Java Interview Questions For Freshers.pdf")
print("Documents loaded:", len(documents))


# Split documents
chunks = split_documents(documents)
print("Documents split into chunks:", len(chunks))


# Embeddings
embeddings_model = get_embedding_model()
print("Embeddings model loaded.", embeddings_model)


# Vector Store
vector_store = create_vector_store(chunks, embeddings_model)
print("Vector store created with chunks:", vector_store.index.ntotal)


# Retriever
retriever = create_retriever(vector_store)

# Debug context retrieval
sample_docs = retriever.invoke("What is constructor?")

print("Sample Context retrieved:")
for doc in sample_docs:
    print(doc.page_content)


# LLM
llm = load_llm_model()
print("LLM model loaded.", llm)


# Prompt
prompt = get_prompt()
print("Prompt template created.", prompt)


# RAG Chain
rag_chain = create_rag_chain(llm, retriever, prompt)
print("RAG chain created.", rag_chain)


# User Interaction Loop
while True:
    question = input("\nAsk your question:")

    if question.lower() == "exit":
        break

    answer = rag_chain.invoke(question)
    print(answer)
