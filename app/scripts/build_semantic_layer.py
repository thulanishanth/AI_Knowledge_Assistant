import pandas as pd
import json
from pathlib import Path

def generate_schema_json(csv_path: str, output_path: str):
    df = pd.read_csv(csv_path)
    
    schema_dict = {
        "table_name": "hotel_reservations",
        "description": "Contains historical and current hotel booking records.",
        "business_rules": [
            "Revenue = avg_price_per_room * (no_of_week_nights + no_of_weekend_nights)"
        ],
        "columns": {}
    }

    for col in df.columns:
        col_type = str(df[col].dtype)
        sql_type = 'FLOAT' if 'float' in col_type else 'INT' if 'int' in col_type else 'VARCHAR'
            
        col_meta = {
            "type": sql_type,
            "business_meaning": f"TODO: Describe {col}",
            "synonyms": []
        }
        
        # Grab unique categorical values or random numeric samples
        if sql_type == 'VARCHAR' or df[col].nunique() < 10:
            col_meta["allowed_values"] = df[col].dropna().unique().tolist()[:6]
        else:
            col_meta["sample_values"] = df[col].dropna().sample(min(3, len(df))).tolist()
            
        schema_dict["columns"][col] = col_meta

    # Save to disk
    with open(output_path, 'w') as f:
        json.dump(schema_dict, f, indent=2)
    print(f"✅ Semantic Dictionary saved to {output_path}")

if __name__ == "__main__":
    # Adjust paths based on where you run the script
    generate_schema_json('../Hotel_Reservations[2].csv', '../data/semantic_dictionary.json')