
Here is a **professional `CHANGELOG.md`** you can use for your project. It follows the common standard **Keep a Changelog** format and semantic versioning.

---

# Changelog

All notable changes to this project will be documented in this file.

The format is based on **Keep a Changelog**
[https://keepachangelog.com/en/1.1.0/](https://keepachangelog.com/en/1.1.0/)

This project follows **Semantic Versioning**
[https://semver.org/](https://semver.org/)

---

# [Unreleased]

## Planned

* Web UI interface (Streamlit or React)
* Multi-document ingestion
* Persistent vector database
* API version using FastAPI
* Deployment with Docker

---

# [1.0.0] - 2026-02-22

## Added

* Initial release of **AI Knowledge Assistant**
* Implemented Retrieval-Augmented Generation (RAG) architecture
* Added PDF document loader using PyPDFLoader
* Implemented document chunking with RecursiveCharacterTextSplitter
* Added embedding model integration using HuggingFaceEndpointEmbeddings
* Implemented FAISS vector store for semantic search
* Added retriever module for context retrieval
* Integrated Qwen2.5-7B-Instruct LLM
* Created prompt template for Java interview explanations
* Implemented RAG chain using LangChain pipeline
* Added interactive CLI chatbot interface
* Implemented logging system with file and console output
* Added sample retrieval test before chatbot loop
* Added Pylint configuration for code quality

## Project Modules Introduced

* embeddings.py
* llm.py
* loader.py
* splitter.py
* vector_store.py
* retriever.py
* prompt.py
* rag_chain.py
* logger.py
* main.py

## Documentation

* Added README.md
* Added CODE_OF_CONDUCT.md
* Added .env.example
* Added project architecture documentation

---

# [0.1.0] - 2026-02-20

## Added

* Initial project setup
* LangChain environment configuration
* Hugging Face API integration
* Basic RAG pipeline prototype
* PDF loading and testing
* Initial embedding experiments

## Improved

* Modularized code structure
* Added logging support
* Refactored project layout

---

# Versioning Guide

This project uses **Semantic Versioning**:

```
MAJOR.MINOR.PATCH
```

Example:

```
1.0.0
```

Meaning:

MAJOR → Breaking changes
MINOR → New features
PATCH → Bug fixes

Example updates:

```
1.0.1 → Bug fixes
1.1.0 → New features added
2.0.0 → Major architecture changes
```

---

# How to Update the Changelog

When making changes:

### Added

For new features.

### Changed

For changes in existing functionality.

### Deprecated

For soon-to-be removed features.

### Removed

For removed features.

### Fixed

For bug fixes.

### Security

For security improvements.

Example:

```
## [1.1.0] - 2026-03-01

Added
- FastAPI API endpoint

Improved
- Retrieval accuracy

Fixed
- Embedding loading issue
```

---
