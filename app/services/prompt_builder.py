# app/services/prompt_builder.py
from __future__ import annotations
from typing import Any
from app.infrastructure.repositories.schema_repository import TableSchema

class PromptBuilder:
    """Centralized builder for all LLM prompts used in the application."""

    def build_intent_prompt(self, user_question: str, memory_context: str) -> str:
        return f"""You are the intelligent routing brain of a Hotel Database Assistant.
Your job is to read the user's prompt, figure out what they want, fix their grammar, and return a STRICT JSON response.

Here is the recent conversation memory:
{memory_context}

User's New Prompt: "{user_question}"

Task:
1. Categorize the intent into one of FOUR categories: 
   - "schema_inquiry": If the user asks about the structure of the table.
   - "database_query": If the user is asking for actual data/rows.
   - "greeting": For hello/goodbye.
   - "general_chitchat": For off-topic questions.
2. CONTEXTUAL REWRITE (CRITICAL): If the intent is "database_query", you MUST rewrite the user's prompt into a perfect, STANDALONE English query. 
   - If the user asks a follow-up, you MUST combine it with the memory above to create a fully self-contained question.
3. If it is a greeting or chitchat, write a friendly, helpful direct response.
4. Evaluate the user's prompt and assign a "difficulty_score" from 0 to 100 based on the cognitive load required to answer it.

You MUST return ONLY a valid JSON object. No markdown formatting, no extra text.
Format:
{{
    "intent": "database_query | schema_inquiry | greeting | general_chitchat",
    "corrected_query": "The grammatically perfect version of their question (only if database_query)",
    "direct_response": "A friendly reply (only if greeting or chitchat, otherwise empty)",
    "difficulty_score": 45,
    "is_follow_up": true/false
}}
"""

    def build_sql_prompt(self, question: str, schema: TableSchema, session_context: str) -> str:
        # We combine the live schema and the retrieved ChromaDB rules into the context block
        database_context = f"Strict Database Schema:\n{schema.to_prompt_block()}\n\nRetrieved Business Rules & Examples:\n{session_context or '(None retrieved)'}"

        return f"""You are an expert SQL generator with strong reasoning ability.
Your goal is to convert a natural language question into an accurate SQL query.

=====================
USER QUESTION:
{question}
=====================

DATABASE CONTEXT (STRICTLY FOLLOW):
{database_context}
=====================

THINKING STEPS (internal, do not output):
- Identify relevant tables
- Identify required columns
- Understand business meaning from definitions
- Apply filters (date, status, conditions)
- Decide aggregation (SUM, COUNT, etc.)
- Construct correct joins

=====================
RULES:
- Use ONLY provided schema
- Do NOT hallucinate columns/tables
- Use proper SQL syntax
- Prefer simple and correct queries over complex ones
- Handle NULLs if needed
- If unsure, return exactly: SELECT 'ERROR: Insufficient context' AS message;

=====================
OUTPUT FORMAT:
SQL QUERY ONLY. NO explanation. Wrap the SQL in a ```sql block."""

    def build_sql_repair_prompt(self, question: str, schema: TableSchema, session_context: str, invalid_sql: str, errors: list[str]) -> str:
        database_context = f"Strict Database Schema:\n{schema.to_prompt_block()}\n\nRetrieved Business Rules & Examples:\n{session_context or '(None retrieved)'}"
        
        return f"""You are an expert SQL generator. Your previous query failed validation.
Repair the invalid SQL and return one valid MySQL SELECT query only.

=====================
USER QUESTION:
{question}
=====================
DATABASE CONTEXT:
{database_context}
=====================
INVALID SQL:
{invalid_sql}
=====================
VALIDATION ERRORS:
{'; '.join(errors)}
=====================

Fix the errors and output ONLY the corrected SQL wrapped in a ```sql block."""

    def build_synthesis_prompt(self, question: str, data_preview: str) -> str:
        return (
            f"You are a helpful, professional Data Assistant.\n"
            f"The user asked: '{question}'\n"
            f"The database returned this raw data: {data_preview}\n\n"
            f"Write a friendly 1 to 2 sentence human explanation of this data answering the user's question. "
            f"Do NOT write markdown tables. Do NOT mention JSON or raw SQL. Just speak naturally."
        )