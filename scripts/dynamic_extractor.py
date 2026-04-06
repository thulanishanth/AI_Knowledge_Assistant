import pandas as pd
import json
import argparse
from pathlib import Path

def infer_sql_type(pd_dtype):
    """Maps Pandas data types to generic SQL data types."""
    if pd.api.types.is_integer_dtype(pd_dtype):
        return "int"
    elif pd.api.types.is_float_dtype(pd_dtype):
        return "decimal"
    elif pd.api.types.is_bool_dtype(pd_dtype):
        return "boolean"
    elif pd.api.types.is_datetime64_any_dtype(pd_dtype):
        return "datetime"
    else:
        return "varchar"

def extract_context(file_path: str, output_path: str = "app/data/business_context.json"):
    """Reads a CSV, analyzes its structure, and scaffolds the semantic JSON."""
    
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        print(f"❌ Error: File {file_path} not found.")
        return

    print(f"📊 Analyzing {file_path_obj.name}...")
    
    # 1. Load the data dynamically
    df = pd.read_csv(file_path)
    
    # Derive a table name from the file name (e.g., "hotel_reservations")
    table_name = file_path_obj.stem.lower().replace(" ", "_").replace("[1]", "")
    
    schema_lines = []
    semantic_draft = []
    
    print(f"🔍 Found {len(df.columns)} columns. Inferring types and metadata...")
    
    # 2. Analyze columns dynamically
    for col in df.columns:
        dtype = infer_sql_type(df[col].dtype)
        schema_lines.append(f"- {col} ({dtype})")
        
        # 3. Smart Metadata Extraction:
        # If it's a text column with a low number of unique values, it's a category.
        # We must explicitly tell the LLM what these categories are to prevent hallucinations.
        if dtype == "varchar":
            unique_vals = df[col].dropna().unique()
            if 0 < len(unique_vals) <= 12:
                samples = ", ".join([f"'{str(v)}'" for v in unique_vals])
                semantic_draft.append(
                    f"Column Meaning '{col}': Categorizes data into specific values: {samples}."
                )
            else:
                # If there are too many unique values (like an ID), just grab 2 examples
                samples = ", ".join([f"'{str(v)}'" for v in df[col].dropna().head(2).tolist()])
                semantic_draft.append(f"Column Format '{col}': Example values look like {samples}.")

    # 4. Construct the Semantic Knowledge JSON
    business_context = {
        "dataset_name": table_name,
        "auto_extracted_schema": schema_lines,
        "semantic_layer": semantic_draft + [
            "TODO: Add human-defined metric formulas here (e.g., 'Revenue' = price * nights)"
        ],
        "constraints": [
            "Rule: Always limit queries to 10 rows unless otherwise specified using LIMIT 10.",
            "TODO: Add human-defined business rules here (e.g., 'Always exclude deleted/canceled records')"
        ],
        "examples": [
            f"Question: Show me the first 5 records from {table_name}.\nSQL: SELECT * FROM {table_name} LIMIT 5;"
        ]
    }

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 5. Save the structured JSON
    with open(output_path, "w") as f:
        json.dump(business_context, f, indent=4)
        
    print(f"✅ Successfully extracted schema and drafted semantic context to {output_path}")
    print("👉 NEXT STEP: Open the JSON file, review the auto-extracted rules, and fill in the 'TODO' metrics!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract schema and semantics from a dataset.")
    parser.add_argument("file_path", help="Path to the CSV file to analyze")
    parser.add_argument("--out", default="app/data/business_context.json", help="Path to save the JSON output")
    
    args = parser.parse_args()
    extract_context(args.file_path, args.out)