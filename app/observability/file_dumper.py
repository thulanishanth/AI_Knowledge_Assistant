# app/observability/file_dumper.py
import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from app.core.logging import get_logger

logger = get_logger(__name__)

# =====================================================================
# TIMEZONE & SERVER START (Executes once per server start)
# =====================================================================

# 1. Define your exact local timezone (Hyderabad, IST)
LOCAL_TZ = ZoneInfo("Asia/Kolkata")

# 2. Capture the exact runtime start time of the server
SERVER_START_TIME = datetime.now(LOCAL_TZ)
SERVER_START_STR = SERVER_START_TIME.strftime("%Y-%m-%d %H:%M:%S %Z")

# =====================================================================
# DYNAMIC PATH GENERATION 
# =====================================================================

# 1. Define the base logs directory
BASE_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "dumps"

# 2. Get today's local date and create the daily folder (e.g., "2026-04-16")
today_str = SERVER_START_TIME.strftime("%Y-%m-%d")
DAILY_DIR = BASE_LOG_DIR / today_str
DAILY_DIR.mkdir(parents=True, exist_ok=True)

# 3. Figure out the "Trial" number for this specific server run
existing_trials = [d for d in DAILY_DIR.iterdir() if d.is_dir() and d.name.startswith("trial_")]
trial_numbers = []

for d in existing_trials:
    try:
        # Extract the number from "trial_1", "trial_2", etc.
        num = int(d.name.split("_")[1])
        trial_numbers.append(num)
    except ValueError:
        pass

# Increment to the next trial number (start at 1 if none exist)
next_trial_num = max(trial_numbers) + 1 if trial_numbers else 1

# 4. Create the specific trial directory for this server session
TRIAL_DIR = DAILY_DIR / f"trial_{next_trial_num}"
TRIAL_DIR.mkdir(parents=True, exist_ok=True)

logger.info(f"Initialized file dumper. Logging to: {TRIAL_DIR}")

# =====================================================================
# FILE TARGETS FOR THIS SESSION
# =====================================================================

# The 6 separate files (1-to-1 line mapping)
USER_FILE = TRIAL_DIR / "1.user_inputs.txt"
AI_FILE = TRIAL_DIR / "2.ai_responses.txt"
TIME_FILE = TRIAL_DIR / "3.timestamps.txt"
PROMPT_FILE = TRIAL_DIR / "4.full_prompts.txt"
RAG_FILE = TRIAL_DIR / "5.rag_context_only.txt"
SQL_FILE = TRIAL_DIR / "6.generated_sql.txt"

#NEW: Separate files for LLM Metadata and Human Readable Prompts
LLM_METADATA_FILE = TRIAL_DIR / "7.llm_execution_metrics.txt"
HUMAN_PROMPT_FILE = TRIAL_DIR / "8.human_readable_prompts.txt"

# The Master Files
ALL_IN_ONE_TXT = TRIAL_DIR / "9.human_readable_history.txt"
ALL_IN_ONE_JSON = TRIAL_DIR / "10.all_in_one_history.jsonl"

# --- Write the Server Boot Header to the Human Readable File ---
with open(ALL_IN_ONE_TXT, "a", encoding="utf-8") as f:
    f.write(f"################################################################################\n")
    f.write(f"SERVER SESSION STARTED AT: {SERVER_START_STR}\n")
    f.write(f"TRIAL RUN: {next_trial_num}\n")
    f.write(f"################################################################################\n\n")


# =====================================================================
# EXECUTION LOGIC
# =====================================================================

def _sanitize_for_txt(text: str) -> str:
    """Replaces newlines with a literal '\\n' string so the text stays on exactly ONE line."""
    if not text:
        return "None"
    return str(text).replace("\n", "\\n").replace("\r", "").strip()

def sync_dump_to_txt(user_query: str, ai_response: str, full_prompt: str, rag_context: str, generated_sql: str, execution_status: str,
                     human_readable_prompt: str,llm_model_name: str,max_context_window: int, estimated_tokens: int):
    """Synchronously appends the data to all files in the current trial directory."""
    try:
        # Get the EXACT local time the user's query finished executing
        now_dt = datetime.now(LOCAL_TZ)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        now_iso = now_dt.isoformat() # Keep ISO format for JSON parsers

        # 1. Sanitize for the 1-line .txt files
        clean_query = _sanitize_for_txt(user_query)
        clean_response = _sanitize_for_txt(ai_response)
        clean_prompt = _sanitize_for_txt(full_prompt)
        clean_rag = _sanitize_for_txt(rag_context)
        clean_sql = _sanitize_for_txt(generated_sql)

        # 2. Write to the 6 separate files
        with open(USER_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_query}\n")
        with open(AI_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_response}\n")
        with open(TIME_FILE, "a", encoding="utf-8") as f: f.write(f"{now_str}\n")
        with open(PROMPT_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_prompt}\n")
        with open(RAG_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_rag}\n")
        with open(SQL_FILE, "a", encoding="utf-8") as f: f.write(f"{clean_sql}\n")
        
        #Write LLM Execution Metrics to its Separate File
        metadata_line = f"[{now_str}] Model: {llm_model_name} | Max Window: {max_context_window} | Tokens Used: {estimated_tokens}\n"
        with open(LLM_METADATA_FILE, "a", encoding="utf-8") as f: 
            f.write(metadata_line)

        #  Write the Human Readable Prompt to its Separate File (Multi-line)
        prompt_block = (
            f"--- PROMPT DUMP: {now_str} ---\n"
            f"{human_readable_prompt or 'None'}\n"
            f"----------------------------------------\n\n"
        )
        with open(HUMAN_PROMPT_FILE, "a", encoding="utf-8") as f:
            f.write(prompt_block)

        # 3. Create the Human-Readable block
        human_readable_block = (
            f"================================================================================\n"
            f"EXECUTION TIMESTAMP: {now_str}\n"
            f"LLM DETAILS: {llm_model_name} (Used ~{estimated_tokens}/{max_context_window} tokens)\n"
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
            "timestamp": now_iso,
            "local_time": now_str,
            "llm_model": llm_model_name,
            "max_context_window": max_context_window,
            "estimated_tokens": estimated_tokens,
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

async def dump_conversation(user_query: str, ai_response: str, full_prompt: str = "", rag_context: str = "", generated_sql: str = "", execution_status: str = "",
                            human_readable_prompt: str = "",llm_model_name: str = "unknown",max_context_window: int = 0,estimated_tokens: int = 0):
    """Async wrapper so it doesn't block your FastAPI event loop."""
    await asyncio.to_thread(sync_dump_to_txt, user_query, ai_response, full_prompt, rag_context, generated_sql, execution_status,
                            human_readable_prompt,llm_model_name, max_context_window, estimated_tokens)