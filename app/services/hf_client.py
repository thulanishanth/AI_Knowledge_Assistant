"""Client for interacting with HuggingFace Inference API with Retries."""
import time
import requests
from app.config import settings

def generate_sql(question: str, retries: int = 3, delay: int = 10) -> str:
    """Sends a question to Qwen with a retry loop for model loading."""
    # Updated API Endpoint below
    print(f"DEBUG - API Key starts with: {str(settings.hf_api_key)[:7]}")
    print(f"DEBUG - Model is: {settings.hf_model}")
    
    api_url = f"https://router.huggingface.co/hf-inference/models/{settings.hf_model}" 
    # Note: Sometimes it requires /hf-inference/ path depending on the exact model routing, 
    # but try the base router URL if that fails: f"https://router.huggingface.co/models/{settings.hf_model}"
    headers = {"Authorization": f"Bearer {settings.hf_api_key}"}

    prompt = f"""
    You are a SQL expert. Write a standard MySQL SELECT query for the following:
    Table: hotel_reservations
    Columns: booking_id, no_of_adults, no_of_children, no_of_weekend_nights, no_of_week_nights, 
    type_of_meal_plan, required_car_parking_space, room_type_reserved, lead_time, arrival_year, 
    arrival_month, arrival_date, market_segment_type, repeated_guest, avg_price_per_room, 
    no_of_special_requests, booking_status

    Question: {question}
    SQL Query:"""

    for attempt in range(retries):
        response = requests.post(api_url, headers=headers, json={"inputs": prompt}, timeout=30)
        
        # If the API returns a standard error before JSON parsing, handle it safely
        try:
            result = response.json()
        except ValueError:
            raise ValueError(f"Failed to parse JSON response. Status Code: {response.status_code}, Text: {response.text}")

        if response.status_code == 200:
            # Success!
            generated_text = result[0].get("generated_text", "") if isinstance(result, list) else result.get("generated_text", "")
            return generated_text.split("SQL Query:")[-1].strip().replace("```sql", "").replace("```", "")

        if response.status_code == 503:
            # Model is loading, wait and try again
            print(f"Model is loading (Attempt {attempt + 1}/{retries}). Waiting {delay}s...")
            time.sleep(delay)
            continue
        
        # Other error (Auth, Rate Limit, etc.)
        raise ValueError(f"HF API Error {response.status_code}: {result}")

    raise ValueError("AI Model failed to load after multiple attempts. Please try again in a minute.")