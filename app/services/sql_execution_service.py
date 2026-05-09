# app/services/sql_execution_service.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.core.settings import settings
from app.core.logging import get_logger
from app.infrastructure.database import db_manager  # Centralized connection manager

logger = get_logger(__name__)

@dataclass(slots=True)
class QueryExecutionResult:
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    execution_ms: float
    executed_sql: str
    selected_columns: list[str] = field(default_factory=list)
    error_message: str | None = None


class SQLExecutionService:
    """
    Universal execution layer supporting relational DBs (Postgres, MySQL, Snowflake) 
    and flat files (CSV, Parquet, Excel, JSON) via DuckDB.
    """

    def execute(self, sql_query: str, source_uri: str, category: str = "relational_db") -> QueryExecutionResult:
        """Routes the query to the correct runtime engine and executes it."""
        started = time.perf_counter()
        bounded_sql = self._ensure_limit(sql_query)
        
        try:
            # 1. Route to the correct Execution Engine
            if category in ["tabular", "tabular_file", "cloud_storage"]:
                rows = self._execute_duckdb(bounded_sql, source_uri)
            elif category == "relational_db":
                rows = self._execute_sqlalchemy(bounded_sql, source_uri)
            else:
                raise ValueError(f"Automated SQL execution is not supported for category: {category}")
                
            # 2. Handle Truncations
            fetch_limit = settings.max_query_results + 1
            truncated = len(rows) >= fetch_limit
            
            # --- SMART LOGIC: Show COUNT(*) for massive truncated raw datasets ---
            is_aggregated = self._is_aggregated(sql_query)
            
            if truncated and not is_aggregated:
                # Strip limits via AST and run a COUNT query
                try:
                    ast_no_limit = sqlglot.parse_one(sql_query, read="mysql")
                    if ast_no_limit.args.get("limit"):
                        ast_no_limit.args["limit"] = None
                    clean_sql = ast_no_limit.sql(dialect="mysql")
                except Exception as e:
                    logger.warning("Failed to strip limit using AST: %s. Using raw query.", e)
                    clean_sql = sql_query.strip().rstrip(";")
                
                count_sql = f"SELECT COUNT(*) AS `Total_Matching_Records` FROM ({clean_sql}) AS subq;"
                
                if category in ["tabular", "tabular_file", "cloud_storage"]:
                    count_rows = self._execute_duckdb(count_sql, source_uri)
                else:
                    count_rows = self._execute_sqlalchemy(count_sql, source_uri)
                    
                # Format with commas (e.g. "11,885")
                count_val = count_rows[0].get("Total_Matching_Records", 0) if count_rows else 0
                formatted_count = f"{count_val:,}" if isinstance(count_val, (int, float)) else str(count_val)
                
                return QueryExecutionResult(
                    rows=[{"Total Matching Records": formatted_count}],
                    row_count=1,
                    truncated=False,
                    execution_ms=(time.perf_counter() - started) * 1000,
                    executed_sql=count_sql,
                    selected_columns=["Total Matching Records"]
                )
            # ---------------------------------------------------------------------

            limited_rows = rows[:settings.max_query_results]
            selected_cols = list(limited_rows[0].keys()) if limited_rows else []
            
            return QueryExecutionResult(
                rows=limited_rows,
                row_count=len(limited_rows),
                truncated=truncated,
                execution_ms=(time.perf_counter() - started) * 1000,
                executed_sql=bounded_sql,
                selected_columns=selected_cols
            )
            
        except Exception as exc:
            logger.error("Execution failed on [%s]: %s", source_uri, exc)
            # Return the error gracefully so the Orchestrator can trigger Self-Healing!
            return QueryExecutionResult(
                rows=[],
                row_count=0,
                truncated=False,
                execution_ms=(time.perf_counter() - started) * 1000,
                executed_sql=bounded_sql,
                error_message=str(exc)
            )

    def _execute_duckdb(self, sql_query: str, file_path: str) -> list[dict[str, Any]]:
        """Executes SQL against flat files (CSV, Parquet, JSON, Excel) in memory using DuckDB."""
        try:
            import duckdb
        except ImportError:
            raise ImportError("DuckDB is missing. Run: pip install duckdb")
            
        # The LLM generated SQL expecting a table name. We use the file's stem as the table.
        table_name = Path(file_path).stem.replace(" ", "_")
        ext = Path(file_path).suffix.lower()
        
        conn = duckdb.connect(database=':memory:')
        
        try:
            # Mount the file as a virtual table in DuckDB
            if ext == '.csv':
                conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM read_csv_auto('{file_path}')")
            elif ext == '.parquet':
                conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM read_parquet('{file_path}')")
            elif ext == '.json':
                conn.execute(f"CREATE VIEW {table_name} AS SELECT * FROM read_json_auto('{file_path}')")
            elif ext in ['.xls', '.xlsx']:
                import pandas as pd
                df = pd.read_excel(file_path)
                conn.register(table_name, df)
            else:
                raise ValueError(f"DuckDB engine cannot directly mount {ext} files.")
            
            # Execute the LLM's query against the virtual table
            result_df = conn.execute(sql_query).fetchdf()
            
            # Handle timestamps/dates securely for JSON serialization
            result_df = result_df.astype(object).where(result_df.notnull(), None)
            return result_df.to_dict(orient='records')
            
        finally:
            conn.close()
            
    def _execute_sqlalchemy(self, sql_query: str, uri: str) -> list[dict[str, Any]]:
        """Executes SQL against standard remote relational databases using the shared connection pool."""
        try:
            # We now safely use the db_manager imported at the top of the file
            with db_manager.engine.connect() as conn:
                
                # Apply timeout constraints
                if "mysql" in uri:
                    conn.execute(sa.text(f"SET SESSION MAX_EXECUTION_TIME={settings.db_query_timeout_ms}"))
                elif "postgres" in uri:
                    conn.execute(sa.text(f"SET statement_timeout = {settings.db_query_timeout_ms}"))
                    
                result = conn.execute(sa.text(sql_query))
                
                return [dict(row._mapping) for row in result]
                
        except SQLAlchemyError as e:
            logger.error("Database execution failed: %s", e)
            raise RuntimeError(f"Database execution error: {e}") from e

    @staticmethod
    def _ensure_limit(sql_query: str) -> str:
        """Safely appends a LIMIT clause using AST parsing."""
        fetch_limit = settings.max_query_results + 1
        try:
            parsed = sqlglot.parse_one(sql_query, read="mysql")
            
            # If the AST already has a LIMIT node, leave it alone
            if parsed.args.get("limit"):
                return parsed.sql(dialect="mysql") + ";"
                
            # Otherwise, mathematically attach a limit to the tree
            parsed = parsed.limit(fetch_limit)
            return parsed.sql(dialect="mysql") + ";"
            
        except Exception as e:
            logger.warning("AST limit injection failed: %s. Using string fallback.", e)
            # Fallback string manipulation if parsing fails
            sql = (sql_query or "").strip().rstrip(";")
            if "limit " not in sql.lower():
                return f"{sql} LIMIT {fetch_limit};"
            return f"{sql};"

    @staticmethod
    def _is_aggregated(sql_query: str) -> bool:
        """Determines if a query is aggregated by searching the AST."""
        try:
            parsed = sqlglot.parse_one(sql_query, read="mysql")
            # Look for GROUP BY node or any Aggregate Function node (COUNT, SUM, AVG)
            has_group = bool(parsed.find(exp.Group))
            has_agg = bool(parsed.find(exp.AggFunc))
            return has_group or has_agg
        except Exception as e:
            logger.warning("AST aggregation check failed: %s. Using string fallback.", e)
            # Fallback string match if parsing fails
            sq = sql_query.lower()
            return any(k in sq for k in ["group by", "count(", "sum(", "avg(", "max(", "min("])

_execution_service = SQLExecutionService()

def execute_safe_query(sql_query: str, source_uri: str, category: str = "relational_db") -> list[dict[str, Any]] | str:
    """Backward-compatible execution helper."""
    try:
        return _execution_service.execute(sql_query, source_uri, category).rows
    except Exception as exc: 
        return str(exc)