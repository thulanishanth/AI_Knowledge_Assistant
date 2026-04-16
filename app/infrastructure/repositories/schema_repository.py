# app/infrastructure/repositories/schema_repository.py
"""Repository for dynamic schema inspection backed by Universal Context JSON."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import settings

logger = get_logger(__name__)

_TEXT_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum"}
_BUSINESS_CONTEXT_PATH = Path(__file__).resolve().parents[2] / "data" / "business_context.json"

# Matches the "- column_name (data_type)" format from our dynamic extractor
_CONTEXT_SCHEMA_PATTERN = re.compile(r"-\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]+)\)")


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    sample_values: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_numeric(self) -> bool:
        return self.data_type.lower() in {
            "int", "integer", "bigint", "smallint", "tinyint", 
            "mediumint", "decimal", "float", "double", "numeric"
        }

    @property
    def is_text(self) -> bool:
        return self.data_type.lower() in _TEXT_TYPES


@dataclass(frozen=True, slots=True)
class TableSchema:
    """Represents the complete schema layout, supporting multiple tables for Relational DBs."""
    dataset_name: str
    dialect: str
    tables: dict[str, tuple[ColumnProfile, ...]]

    @property
    def table_name(self) -> str:
        """Backward compatibility: Returns the first table name if requested."""
        return next(iter(self.tables.keys()), self.dataset_name) if self.tables else self.dataset_name

    @property
    def fingerprint(self) -> str:
        """Generates a unique signature for caching queries."""
        parts = [f"{self.dataset_name}:{self.dialect}"]
        for table, cols in self.tables.items():
            for col in cols:
                parts.append(f"{table}.{col.name}:{col.data_type}")
        return "|".join(parts)

    def to_prompt_block(self) -> str:
        """Renders the entire multi-table schema for the LLM Prompt."""
        lines = [
            f"Target Execution Dialect: {self.dialect.upper()}",
            f"Dataset Name: {self.dataset_name}",
            "Schema Structure:"
        ]
        
        for table_name, columns in self.tables.items():
            lines.append(f"\nTable: {table_name}")
            for col in columns:
                key_marker = " [PRIMARY KEY]" if col.is_primary_key else ""
                lines.append(f"  - {col.name} ({col.data_type}){key_marker}")
                if col.sample_values:
                    joined = ", ".join(f"'{val}'" for val in col.sample_values)
                    lines.append(f"    Sample values: {joined}")
                    
        return "\n".join(lines)


class SchemaRepository:
    """Reads structured schema, dialect, and data profiles from the Universal JSON."""

    def get_active_schema(self) -> TableSchema:
        if not _BUSINESS_CONTEXT_PATH.exists():
            logger.warning("business_context.json not found! Please run dynamic_extractor.py first.")
            return TableSchema(dataset_name="unknown", dialect="unknown", tables={})

        try:
            with open(_BUSINESS_CONTEXT_PATH, "r", encoding="utf-8") as f:
                context_data = json.load(f)

            dataset_name = context_data.get("dataset", settings.db_table)
            dialect = context_data.get("target_dialect", "unknown")
            raw_schema = context_data.get("schema", {})
            data_profile = context_data.get("data_profile", {})

            tables: dict[str, tuple[ColumnProfile, ...]] = {}

            # Parse out all tables/collections
            for table_name, columns_list in raw_schema.items():
                parsed_columns: list[ColumnProfile] = []
                
                for col_str in columns_list:
                    match = _CONTEXT_SCHEMA_PATTERN.match(str(col_str).strip())
                    if not match:
                        continue

                    col_name = match.group(1)
                    data_type = match.group(2).strip().lower()
                    
                    # Safely grab sample values directly from the new data_profile feature!
                    sample_values = ()
                    if col_name in data_profile:
                        samples = data_profile[col_name].get("sample_values", [])
                        sample_values = tuple(str(v) for v in samples if v)

                    parsed_columns.append(
                        ColumnProfile(
                            name=col_name,
                            data_type=data_type,
                            nullable=True,               # Assumed true unless defined otherwise
                            is_primary_key="id" in col_name.lower(), # Simple heuristic
                            sample_values=sample_values,
                        )
                    )
                
                tables[table_name] = tuple(parsed_columns)

            schema_obj = TableSchema(
                dataset_name=dataset_name,
                dialect=dialect,
                tables=tables
            )
            
            logger.info("Successfully loaded Universal Schema mapping into repository.")
            return schema_obj

        except Exception as exc:
            logger.exception("Failed to parse Universal Schema JSON: %s", exc)
            return TableSchema(dataset_name="error", dialect="error", tables={})