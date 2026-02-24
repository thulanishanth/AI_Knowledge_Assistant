import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_URL = f"https://api-inference.huggingface.co/models/{os.getenv('HF_MODEL')}"
HEADERS = {"Authorization": f"Bearer {os.getenv('HF_API_KEY')}"}

def generate_sql(question, schema):
    prompt = f"""
You are a SQL expert.
Database schema:
{schema}

User question:
{question}

Return only SQL SELECT query.
"""

    payload = {"inputs": prompt}

    response = requests.post(API_URL, headers=HEADERS, json=payload)
    return response.json()