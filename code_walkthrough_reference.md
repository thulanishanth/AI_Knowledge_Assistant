# 📘 RAG System – Complete Code Walkthrough

## 📌 Overview

This project implements a **Retrieval-Augmented Generation (RAG)** pipeline using:

* HuggingFace Embeddings
* FAISS vector store
* LangChain retriever
* Prompt templates
* Qwen LLM (via HuggingFace Endpoint)

The system answers questions using only provided PDF context.

---

# 🏗️ High-Level Architecture

```
PDF
 ↓
Loader
 ↓
Splitter
 ↓
Embeddings
 ↓
FAISS Vector Store
 ↓
Retriever
 ↓
Prompt Template
 ↓
LLM
 ↓
Final Answer
```

---

# 🔍 Execution Flow (End-to-End)

When a user asks:

```python
rag_chain.invoke("What is abstraction?")
```

The following happens:

1. Question is received
2. Question → embedding
3. Similar chunks retrieved from FAISS
4. Retrieved chunks formatted as context
5. Prompt is constructed
6. LLM generates response
7. Output parsed into string

---

# 📂 Module-by-Module Deep Dive

---

# 1️⃣ Logger Module

📄 Source: 

### Purpose

Centralized logging for debugging and observability.

### What Happens Internally

```python
logging.basicConfig(...)
```

This configures:

* Logging level: `INFO`
* Log format: timestamp, module, level, message
* Handlers:

  * FileHandler → logs/app.log
  * StreamHandler → console output

### Key Function

```python
def get_logger(name: str):
    return logging.getLogger(name)
```

Each module calls:

```python
logger = get_logger(__name__)
```

This ensures logs are namespaced per module.

---

# 2️⃣ Document Loader

📄 Source: 

```python
loader = PyPDFLoader(file_path)
documents = loader.load()
```

### What Happens Internally

* Reads the PDF
* Splits it page by page
* Returns:

```python
List[Document]
```

Each Document contains:

```python
Document(
    page_content="Text from page",
    metadata={"source": "...", "page": 1}
)
```

### Data in Memory

```python
[
  Document(page_content="Java is OOP...", metadata={}),
  Document(page_content="Encapsulation means...", metadata={})
]
```

---

# 3️⃣ Document Splitter

📄 Source: 

```python
RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200
)
```

### Why Needed?

LLMs have token limits.
Large pages must be broken into smaller chunks.

### How It Works

* Splits text every 1000 characters
* Keeps 200 characters overlap
* Ensures continuity across chunks

### Output

```python
chunks = List[Document]
```

Each chunk still has metadata.

---

# 4️⃣ Embedding Model

📄 Source: 

```python
HuggingFaceEndpointEmbeddings(
    model="sentence-transformers/all-MiniLM-L6-v2",
    task="feature-extraction"
)
```

### What It Does

Converts text into numerical vector representation.

Example:

```
"Java is OOP"
↓
[0.124, -0.992, 0.223, ...]  # 384-dimension vector
```

### Technical Details

* Uses HuggingFace hosted endpoint
* Model: MiniLM-L6-v2
* Task: feature extraction
* Returns float vector array

These vectors represent semantic meaning.

---

# 5️⃣ Vector Store (FAISS)

📄 Source: 

```python
FAISS.from_documents(chunks, embedding_model)
```

### What Happens Internally

For each chunk:

1. embedding_model.embed(chunk.text)
2. Store vector in FAISS index

FAISS stores:

```
Index:
Vector dimension: 384
Total vectors: N
```

Check count via:

```python
vector_store.index.ntotal
```

### Why FAISS?

* Fast similarity search
* Approximate Nearest Neighbor (ANN)
* Efficient for large datasets

---

# 6️⃣ Retriever

📄 Source: 

```python
vector_store.as_retriever(search_kwargs={"k": 4})
```

### What It Does

When question comes:

1. Question → embedding
2. Search FAISS for nearest vectors
3. Return top 4 matching chunks

So retriever wraps FAISS with search logic.

---

# 7️⃣ Prompt Template

📄 Source: 

```python
ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{question}")
])
```

### System Prompt Contains:

* Role: Expert Java Instructor
* Rules:

  * Use ONLY context
  * Max 3–5 sentences
  * Say "I don't know" if missing

### At Runtime

If retrieved context is:

```
Polymorphism allows objects...
```

The final prompt becomes:

```
System:
You are expert Java instructor...
Context:
Polymorphism allows objects...

Human:
What is polymorphism?
```

---

# 8️⃣ LLM Model

📄 Source: 

```python
HuggingFaceEndpoint(
    repo_id="Qwen/Qwen2.5-7B-Instruct",
    max_new_tokens=250,
    temperature=0.1
)
```

### What Happens

* Sends prompt to HuggingFace endpoint
* Qwen model generates output
* temperature=0.1 → low randomness
* max_new_tokens=250 → limit output length

Wrapped with:

```python
ChatHuggingFace(llm=llm)
```

This enables chat-style interaction.

---

# 9️⃣ RAG Chain Assembly (Core Logic)

📄 Source: 

```python
rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough()
    }
    | prompt
    | llm
    | StrOutputParser()
)
```

---

## 🔥 Deep Execution Breakdown

When calling:

```python
rag_chain.invoke("What is abstraction?")
```

### Step 1: Input Received

```
"What is abstraction?"
```

---

### Step 2: Dictionary Mapping

```python
{
  "context": retriever | format_docs,
  "question": RunnablePassthrough()
}
```

Meaning:

* question → passed unchanged
* context → send question to retriever
* retriever returns 4 documents
* format_docs joins them:

```python
"\n\n".join(doc.page_content for doc in docs)
```

Now data becomes:

```python
{
  "context": "chunk1\n\nchunk2\n\nchunk3",
  "question": "What is abstraction?"
}
```

---

### Step 3: Prompt Formatting

Prompt inserts:

* {context}
* {question}

Generates full system + human message.

---

### Step 4: LLM Execution

* Prompt sent to Qwen endpoint
* Model generates response
* Token limit enforced

---

### Step 5: Output Parsing

```python
StrOutputParser()
```

Extracts plain string response.

---

# 🧠 Memory & Data Flow Summary

| Stage        | Input              | Output           |
| ------------ | ------------------ | ---------------- |
| Loader       | PDF file           | List[Document]   |
| Splitter     | Documents          | Smaller chunks   |
| Embedding    | Text               | 384-dim vector   |
| Vector Store | Chunks + vectors   | FAISS index      |
| Retriever    | Question           | Top 4 chunks     |
| Prompt       | Context + Question | Formatted prompt |
| LLM          | Prompt             | Generated text   |
| Parser       | LLM output         | String           |

---

# ⚡ Why This Architecture Reduces Hallucination

Because:

* LLM is forced to use retrieved context
* System prompt restricts output
* If context missing → model says “I don't know”

This grounds the answer in source documents.

---

# 🎯 Key Engineering Strengths

* Modular design
* Clear separation of concerns
* Logging across all modules
* Configurable retriever depth
* Low temperature for factual answers
* Efficient ANN search via FAISS

---

# 🚀 Possible Improvements

* Add persistence for FAISS index
* Add caching for embeddings
* Add streaming response support
* Add evaluation metrics
* Add chunk metadata filtering
* Add retry mechanism for endpoint failures

---

# 📌 Final Summary

This project implements a clean RAG pipeline where:

* Documents are embedded into vector space
* Similar chunks retrieved using FAISS
* Context injected into prompt
* LLM generates grounded response

It follows modern LLM system design principles and is production-extendable.
