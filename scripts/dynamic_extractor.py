# app/scripts/dynamic_extractor.py
import json
import argparse
import os
from pathlib import Path
from urllib.parse import urlparse

# =========================
# LAZY IMPORTS
# =========================
def get_pandas():
    try: import pandas as pd; return pd
    except ImportError: raise ImportError("pip install pandas")

def get_sqlalchemy():
    try: import sqlalchemy as sa; return sa
    except ImportError: raise ImportError("pip install sqlalchemy")

def get_pymongo():
    try: import pymongo; return pymongo
    except ImportError: raise ImportError("pip install pymongo")

# =========================
# UNIVERSAL INPUT ROUTER
# =========================
def identify_input_type(source_string: str):
    parsed = urlparse(source_string)
    scheme = parsed.scheme.lower()
    
    # 1. Cloud Storage
    if scheme in ['s3', 'gs', 'abfs', 'azure']:
        return {"category": "cloud_storage", "path": source_string}

    # 2. Databases
    if scheme:
        # Strip out the specific driver (e.g., '+pymysql' or '+psycopg2') so 'mysql+pymysql' becomes 'mysql'
        base_scheme = scheme.split('+')[0] 
        
        if base_scheme in ['mysql', 'postgresql', 'postgres', 'sqlite', 'oracle', 'mssql', 'snowflake', 'bigquery', 'redshift']:
            return {"category": "relational_db", "path": source_string, "dialect": base_scheme}
        if scheme in ['mongodb', 'couchdb']:
            return {"category": "nosql_db", "path": source_string, "dialect": scheme}
        if scheme in ['neo4j', 'neptune']:
            return {"category": "graph_db", "path": source_string, "dialect": scheme}
        if scheme in ['pinecone', 'weaviate', 'chroma', 'faiss']:
            return {"category": "vector_db", "path": source_string, "dialect": scheme}

    # 3. Local Files
    ext = Path(source_string).suffix.lower()

    if ext in ['.csv', '.tsv', '.xlsx', '.xls', '.parquet', '.orc', '.avro']:
        return {"category": "tabular", "path": source_string, "ext": ext}
    if ext in ['.json', '.jsonl', '.xml', '.yaml']:
        return {"category": "semi_structured", "path": source_string, "ext": ext}
    if ext in ['.txt', '.md', '.pdf', '.docx']:
        return {"category": "unstructured_text", "path": source_string, "ext": ext}
    if ext in ['.jpg', '.png', '.mp4', '.avi', '.mp3', '.wav']:
        return {"category": "media", "path": source_string, "ext": ext}
    if ext in ['.pkl', '.onnx', '.pt', '.h5']:
        return {"category": "ml_model", "path": source_string, "ext": ext}
    
    return {"category": "unknown", "path": source_string, "ext": ext}

# =========================
# DATA PROFILING ENGINE 🔥
# =========================
def profile_dataframe(df):
    profile = {}
    for col in df.columns:
        series = df[col]
        # Handle complex types gracefully
        try: unique_count = int(series.nunique())
        except: unique_count = 0
        
        profile[col] = {
            "dtype": str(series.dtype),
            "nulls": int(series.isnull().sum()),
            "unique": unique_count,
            "sample_values": series.dropna().astype(str).head(5).tolist()
        }
    return profile

# =========================
# SEMANTIC & ONTOLOGY BUILDERS 🔥
# =========================
def generate_semantics(profile):
    semantics = []
    for col, meta in profile.items():
        if 0 < meta["unique"] < 15:  # Slightly expanded threshold for categories
            semantics.append(f"Column '{col}' is categorical with values: {meta['sample_values']}")
        elif "date" in col.lower() or "time" in col.lower():
            semantics.append(f"Column '{col}' represents temporal/date information.")
        elif "id" in col.lower():
            semantics.append(f"Column '{col}' is a unique identifier.")
    return semantics

def build_ontology(columns):
    ontology = {}
    for col in columns:
        key = col.lower()
        synonyms = []
        if "customer" in key or "guest" in key or "client" in key:
            synonyms = ["customer", "guest", "user", "client"]
        elif "booking" in key or "order" in key:
            synonyms = ["booking", "reservation", "order"]
        elif "price" in key or "amount" in key or "cost" in key:
            synonyms = ["price", "cost", "revenue", "amount"]
        
        if synonyms:
            ontology[col] = synonyms
    return ontology

# =========================
# BUSINESS RULE GENERATOR 🔥
# =========================
# =========================
# BUSINESS RULE GENERATOR 🔥
# =========================
def generate_business_rules(profile):
    rules = []
    
    # 1. A global foundational rule to prevent raw count hallucinations
    rules.append("CRITICAL: Never apply status filters (like removing canceled/deleted records) if the user explicitly asks for them, asks for 'total number of rows', 'exact count', or 'all records'.")

    for col, meta in profile.items():
        col_lower = col.lower()
        
        # Safely extract sample values and lowercase them for checking
        samples = [str(v).lower() for v in meta.get("sample_values", [])]
        
        # 2. Smart Status Rule (Softened)
        if "status" in col_lower or "state" in col_lower:
            if any("cancel" in s or "delet" in s or "fail" in s for s in samples):
                rules.append(f"Default to excluding '{col}' values like 'Canceled' or 'Deleted' for general revenue queries, UNLESS the user explicitly asks for canceled/deleted data.")
                
        # 3. Smart Monetary Rule
        if any(keyword in col_lower for keyword in ["price", "amount", "revenue", "cost", "fee"]):
            rules.append(f"'{col}' is a monetary field. Default to SUM() or AVG() when aggregating.")
            
        # 4. Smart Duration Rule
        if any(keyword in col_lower for keyword in ["night", "stay", "duration"]):
            rules.append(f"If calculating total duration, ensure '{col}' is summed correctly.")
            
        # 5. Smart Date/Time Rule
        if "date" in col_lower or "time" in col_lower or "year" in col_lower:
            rules.append(f"Use '{col}' for time-series filtering and aggregations if the user mentions specific timeframes.")

    # Remove duplicates and return
    return list(set(rules))
# =========================
# EXTRACTORS
# =========================
def extract_tabular(path, ext):
    pd = get_pandas()
    if ext == '.csv': df = pd.read_csv(path, nrows=500)
    elif ext in ['.xls', '.xlsx']: df = pd.read_excel(path, nrows=500)
    elif ext == '.parquet': df = pd.read_parquet(path)
    else: raise ValueError(f"Tabular extraction not implemented for {ext}")

    profile = profile_dataframe(df)
    return {
        "schema": {Path(path).stem: list(df.columns)},
        "profile": profile,
        "semantics": generate_semantics(profile),
        "ontology": build_ontology(df.columns),
        "business_rules": generate_business_rules(profile),
        "target_dialect": "duckdb"
    }

def extract_relational(uri, dialect):
    sa = get_sqlalchemy()
    pd = get_pandas()
    engine = sa.create_engine(uri)
    inspector = sa.inspect(engine)

    schema = {}
    master_profile = {}
    
    with engine.connect() as conn:
        for table in inspector.get_table_names():
            schema[table] = [col['name'] for col in inspector.get_columns(table)]
            
            # Fetch a small sample to run through your awesome profiling engine!
            try:
                df = pd.read_sql(f"SELECT * FROM {table} LIMIT 100", conn)
                master_profile.update(profile_dataframe(df))
            except Exception as e:
                print(f"Could not profile table {table}: {e}")

    return {
        "schema": schema,
        "profile": master_profile,
        "semantics": generate_semantics(master_profile),
        "ontology": build_ontology(list(master_profile.keys())),
        "business_rules": generate_business_rules(master_profile),
        "target_dialect": dialect
    }

def extract_nosql(uri):
    """Basic extraction for MongoDB"""
    pymongo = get_pymongo()
    client = pymongo.MongoClient(uri)
    db = client.get_default_database()
    
    schema = {}
    for coll_name in db.list_collection_names():
        doc = db[coll_name].find_one()
        if doc: schema[coll_name] = list(doc.keys())
            
    return {
        "schema": schema,
        "profile": {"notice": "NoSQL deep profiling not implemented"},
        "target_dialect": "mongodb_json"
    }

def extract_metadata_only(path, category):
    """Fallback for unstructured files (PDFs, Media, ML Models)"""
    return {
        "schema": {"file_metadata": ["file_name", "size", "category"]},
        "semantics": [f"This is a {category} file. Standard SQL cannot query its inner contents directly."],
        "target_dialect": "vector_search"
    }

# =========================
# MAIN ENGINE
# =========================
def build_context(source, output):
    print(f"🕵️  Analyzing input: {source}")
    meta = identify_input_type(source)
    category = meta["category"]
    print(f"✅ Detected Category: {category.upper()}")

    result = {}

    try:
        if category == "tabular":
            result = extract_tabular(meta["path"], meta["ext"])
        elif category == "relational_db":
            result = extract_relational(meta["path"], meta["dialect"])
        elif category == "nosql_db":
            result = extract_nosql(meta["path"])
        elif category in ["unstructured_text", "media", "ml_model", "cloud_storage"]:
            result = extract_metadata_only(meta["path"], category)
        else:
            print(f"⚠️ Unsupported category for automated extraction: {category}")
            return
    except Exception as e:
        print(f"❌ Extraction Failed: {e}")
        return

    # =========================
    # FINAL CONTEXT JSON 🔥
    # =========================
    final_context = {
        "dataset": Path(source).stem,
        "source_type": category,
        "target_dialect": result.get("target_dialect", "unknown"),
        "schema": result.get("schema", {}),
        "data_profile": result.get("profile", {}),
        "semantic_layer": result.get("semantics", []),
        "ontology": result.get("ontology", {}),
        "business_rules": result.get("business_rules", []),
        "constraints": [
            "Always LIMIT 10 unless specified",
            "Avoid SELECT *"
        ],
        "examples": []
    }
    
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(final_context, f, indent=4)

    print(f"✅ Context Engine Built Successfully -> {output}")


# =========================
# CLI
# =========================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="The database URI or file path")
    parser.add_argument("--tenant", default="hotel", help="The tenant ID (e.g., hotel, ecommerce)")

    args = parser.parse_args()
    
    # Dynamically build the exact output path your API expects!
    out_path = f"app/data/{args.tenant}_context.json"
    
    build_context(args.source, out_path)