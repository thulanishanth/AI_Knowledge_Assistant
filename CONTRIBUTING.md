

---
## Vijay Sai Contribution

## 🔍 The Linting Stage (Required)

This project strictly utilizes **Pylint** for static code analysis. All code must achieve a passing Pylint score before it can be merged into the `main` branch.

Our custom linting rules are defined in the `.pylintrc` file located in the root directory.

### How to Run the Linter Locally

Before committing your changes, you must run the linter against the entire codebase to ensure you haven't introduced any formatting errors or anti-patterns.

Run the following command from the root directory:

```bash
pylint main.py ai_knowledge_assistant/

```

### 🛑 Common Linting Rules to Keep in Mind

To save time and achieve a 10/10 score on your first pass, please adhere to these project-specific conventions:

* **Docstrings (`C0114`, `C0116`):** Every module (`.py` file) and every function must have a descriptive triple-quoted docstring at the top.
* **Logging Formatting (`W1203`):** Do not use f-strings in logging statements. Use lazy `%s` formatting to conserve memory (e.g., `logger.info("Loaded %s documents", len(docs))`).
* **Blank Lines (`C0304`):** Every Python file must end with exactly one completely empty line.
* **Trailing Whitespace (`C0303`):** Ensure there are no hidden spaces at the ends of your lines.
* **Unused Imports (`W0611`):** Clean up any modules or libraries you imported but did not use.

---

Below is a **“My Contribution” section** you can add to your project (in README or a separate `MY_CONTRIBUTION.md`).
It clearly explains what you contributed to the repository in a **professional, GitHub-ready way**.

---

# Nishanth Contributions

I contributed to improving the structure, documentation, and maintainability of this project by adding essential open-source standards and improving the developer experience.

## Documentation Contributions

### README.md

Created a comprehensive project documentation that includes:

* Project overview and purpose
* RAG system architecture explanation
* Installation and setup guide
* Environment configuration instructions
* Project structure explanation
* Module-by-module explanation
* Usage instructions for running the chatbot
* Future improvements and roadmap

This helps new developers quickly understand and run the project.

---

### CODE_OF_CONDUCT.md

Added a professional Code of Conduct to establish a respectful and inclusive community.

Key contributions:

* Defined community behavior standards
* Listed acceptable and unacceptable behaviors
* Added reporting guidelines
* Defined enforcement process
* Ensured alignment with open-source best practices

This ensures the project follows **industry-standard open-source collaboration guidelines**.

---

### CONTRIBUTING.md

Created contribution guidelines to help developers contribute effectively.

Added guidelines for:

* Forking and cloning the repository
* Setting up the development environment
* Coding standards
* Pull request process
* Commit message conventions
* Reporting issues and bugs

This improves collaboration and makes onboarding easier for new contributors.

---

### CHANGELOG.md

Introduced a structured changelog following **Keep a Changelog** format.

Included:

* Version history
* Major features added
* Improvements
* Bug fixes
* Planned features

This helps track the evolution of the project over time.

---

## Project Structure Improvements

Helped organize the repository into a modular architecture:

```
langchain_module/
    embeddings.py
    llm.py
    loader.py
    splitter.py
    vector_store.py
    retriever.py
    prompt.py
    rag_chain.py
    logger.py
```

Benefits:

* Better code readability
* Easier maintenance
* Clear separation of responsibilities

---

## AI System Architecture Contribution

Worked on improving the **RAG pipeline structure**:

```
Document Loading
        ↓
Document Splitting
        ↓
Embeddings Generation
        ↓
Vector Store (FAISS)
        ↓
Retriever
        ↓
Prompt Engineering
        ↓
LLM Response
```

This design improves:

* Retrieval accuracy
* Response quality
* Scalability

---

---

## Impact of My Contributions

My contributions improved:

* Project documentation quality
* Developer onboarding experience
* Code organization
* Open-source readiness
* Production-level structure

These changes make the project more suitable for:

* Collaboration
* Scaling
* Professional development
* Portfolio demonstration

---

## Shiva Prasad Contribution

## Logging System Contribution

Implemented a centralized logging system.

Features:

* Console logging
* File logging
* Error tracking
* Debug support

Log file:

```
logs/app.log
```

This helps in debugging and monitoring the application.

---