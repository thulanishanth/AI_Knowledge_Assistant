# app/observability/file_dumper.py
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from app.core.logging import get_logger

logger = get_logger(__name__)

# Define where you want the text files to live
LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "dumps"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# The 6 separate files (1-to-1 line mapping)
USER_FILE = LOG_DIR / "user_inputs.txt"
AI_FILE = LOG_DIR / "ai_responses.txt"
TIME_FILE = LOG_DIR / "timestamps.txt"
PROMPT_FILE = LOG_DIR / "full_prompts.txt"
RAG_FILE = LOG_DIR / "rag_context_only.txt"
SQL_FILE = LOG_DIR / "generated_sql.txt" # <--- NEW FILE

# The Master Files
ALL_IN_ONE_TXT = LOG_DIR / "human_readable_history.txt"
ALL_IN_ONE_JSON = LOG_DIR / "all_in_one_history.jsonl"


def _sanitize_for_txt(text: str) -> str:
    """Replaces newlines with a literal '\\n' string so the text stays on exactly ONE line."""
    if not text:
        return "None"
    return str(text).replace("\n", "\\n").replace("\r", "").strip()

def sync_dump_to_txt(user_query: str, ai_response: str, full_prompt: str, rag_context: str, generated_sql: str, execution_status: str):
    """Synchronously appends the data to all files."""
    try:
        now = datetime.now(timezone.utc).isoformat()

        # 1. Sanitize for the 1-line .txt files
        clean_query = _sanitize_for_txt(user_query)
        clean_response = _sanitize_for_txt(ai_response)
        clean_prompt = _sanitize_for_txt(full_prompt)
        clean_rag = _sanitize_for_txt(rag_context)
        clean_sql = _sanitize_for_txt(generated_sql)

        # 2. Write to the 6 separate files
        with open(USER_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_query}\n")
        with open(AI_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_response}\n")
        with open(TIME_FILE, "a", encoding="utf-8") as f: f.write(f"{now}\n")
        with open(PROMPT_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_prompt}\n")
        with open(RAG_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_rag}\n")
        with open(SQL_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_sql}\n") # <-- NEW

        # 3. Create the Human-Readable block
        human_readable_block = (
            f"================================================================================\n"
            f"TIMESTAMP: {now}\n"
            f"--------------------------------------------------------------------------------\n"
            f"USER QUERY:\n{user_query or 'None'}\n"
            f"--------------------------------------------------------------------------------\n"
            f"GENERATED SQL ({execution_status}):\n{generated_sql or 'None'}\n"
            f"--------------------------------------------------------------------------------\n"
            f"AI RESPONSE:\n{ai_response or 'None'}\n"
            f"--------------------------------------------------------------------------------\n"
            f"ISOLATED RAG RULES INJECTED:\n{rag_context or 'None'}\n"
            f"--------------------------------------------------------------------------------\n"
            f"FULL PROMPT SENT TO LLM:\n{full_prompt or 'None'}\n"
            f"================================================================================\n\n"
        )
        with open(ALL_IN_ONE_TXT, "a", encoding="utf-8") as f:
            f.write(human_readable_block)

        # 4. Create the JSONL payload
        log_entry = {
            "timestamp": now,
            "user_query": user_query or "",
            "generated_sql": generated_sql or "",
            "execution_status": execution_status or "",
            "ai_response": ai_response or "",
            "rag_context": rag_context or "",
            "full_prompt": full_prompt or ""
        }
        with open(ALL_IN_ONE_JSON, "a", encoding="utf-8") as f:
            f.write(f"{json.dumps(log_entry, ensure_ascii=False)}\n")
            
    except Exception as e:
        logger.error(f"Failed to dump conversation to txt files: {e}")

async def dump_conversation(user_query: str, ai_response: str, full_prompt: str = "", rag_context: str = "", generated_sql: str = "", execution_status: str = ""):
    """Async wrapper so it doesn't block your FastAPI event loop."""
    await asyncio.to_thread(sync_dump_to_txt, user_query, ai_response, full_prompt, rag_context, generated_sql, execution_status)