# Contributing to AI Knowledge Assistant

Thank you for your interest in contributing! This guide outlines our development workflow and standards.

## Getting Started

### Prerequisites
- Python 3.8 or later
- pip or conda
- Git (internal repository access)

### Setup Development Environment

1. Clone the internal repository and create a feature branch:

```bash
git clone <internal-repo-url>
cd AI_Knowledge_Assistant
git checkout -b feature/your-feature
```

2. Create and activate a virtual environment:

Windows:
```powershell
python -m venv venv
venv\Scripts\activate
```

Mac/Linux:
```bash
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Code Standards

### Linting

All code must meet the repository's linting and style guidelines before merging. Run configured linters locally (e.g., `pylint`, `ruff`, `black`) and address reported issues.

Run the linter locally (example):

```bash
pylint main.py ai_knowledge_assistant/
```

See `.pylintrc` and repository tooling for specific rules.

## Commit and Pull Request Workflow

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make changes and ensure linters and tests pass
3. Commit with clear, descriptive messages
4. Push the branch to the internal remote and open a pull request
5. Ensure all automated checks pass before requesting review

## Reporting Issues

Found a bug? Open an issue in the internal issue tracker including:
- Clear description of the problem
- Steps to reproduce
- Expected vs. actual behavior
- Environment details (Python version, OS, etc.)

