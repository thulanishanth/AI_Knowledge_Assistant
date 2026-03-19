"""Repository for live schema inspection of the configured table."""

from __future__ import annotations

from dataclasses import dataclass, field

from mysql.connector import Error

from app.core.settings import settings
from app.infrastructure.mysql_pool import close_connection, create_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TEXT_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum"}


def _row_value(
    row,
    *keys: str,
    index: int | None = None,
    default=None,
):
    """Safely read from dict/tuple MySQL rows with case-insensitive keys."""
    if isinstance(row, dict):
        normalized = {str(key).lower(): value for key, value in row.items()}
        for key in keys:
            if key.lower() in normalized:
                return normalized[key.lower()]
        return default
    if isinstance(row, (tuple, list)) and index is not None and index < len(row):
        return row[index]
    return default


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    name: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    sample_values: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_numeric(self) -> bool:
        return self.data_type in {
            "int",
            "integer",
            "bigint",
            "smallint",
            "tinyint",
            "mediumint",
            "decimal",
            "float",
            "double",
        }

    @property
    def is_text(self) -> bool:
        return self.data_type in _TEXT_TYPES


@dataclass(frozen=True, slots=True)
class TableSchema:
    database_name: str
    table_name: str
    columns: tuple[ColumnProfile, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

    @property
    def primary_columns(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns if column.is_primary_key)

    @property
    def fingerprint(self) -> str:
        return "|".join(
            f"{column.name}:{column.data_type}:{','.join(column.sample_values)}"
            for column in self.columns
        )

    def to_prompt_block(self) -> str:
        lines = [
            f"Database: {self.database_name}",
            f"Table: {self.table_name}",
            "Columns:",
        ]
        for column in self.columns:
            nullable = "nullable" if column.nullable else "required"
            key_marker = " primary-key" if column.is_primary_key else ""
            lines.append(
                f"- {column.name} ({column.data_type}, {nullable}{key_marker})"
            )
            if column.sample_values:
                joined = ", ".join(column.sample_values)
                lines.append(f"  Sample values: {joined}")
        return "\n".join(lines)


class SchemaRepository:
    """Fetch column metadata and low-cardinality examples from MySQL."""

    def get_active_schema(self) -> TableSchema:
        connection = None
        cursor = None
        try:
            connection = create_db_connection()
            if connection is None:
                raise RuntimeError("Database connection unavailable for schema fetch.")

            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    CASE WHEN k.column_name IS NULL THEN 0 ELSE 1 END AS is_primary_key
                FROM information_schema.columns AS c
                LEFT JOIN information_schema.key_column_usage AS k
                    ON c.table_schema = k.table_schema
                    AND c.table_name = k.table_name
                    AND c.column_name = k.column_name
                    AND k.constraint_name = 'PRIMARY'
                WHERE c.table_schema = %s AND c.table_name = %s
                ORDER BY c.ordinal_position
                """,
                (settings.db_name, settings.db_table),
            )
            rows = cursor.fetchall()
            if not rows:
                raise RuntimeError("Configured table was not found in MySQL metadata.")

            columns: list[ColumnProfile] = []
            for row in rows:
                name = _row_value(row, "column_name", "COLUMN_NAME", index=0)
                data_type = _row_value(row, "data_type", "DATA_TYPE", index=1)
                is_nullable = _row_value(row, "is_nullable", "IS_NULLABLE", index=2)
                is_primary_key = _row_value(
                    row,
                    "is_primary_key",
                    "IS_PRIMARY_KEY",
                    index=3,
                    default=0,
                )

                if name is None or data_type is None:
                    logger.warning("Skipping unexpected schema metadata row: %r", row)
                    continue

                columns.append(
                    ColumnProfile(
                        name=str(name),
                        data_type=str(data_type).lower(),
                        nullable=str(is_nullable).upper() == "YES",
                        is_primary_key=bool(is_primary_key),
                        sample_values=self._load_sample_values(
                            cursor,
                            str(name),
                            str(data_type).lower(),
                        ),
                    )
                )

            if not columns:
                raise RuntimeError("Could not parse schema metadata for the configured table.")

            return TableSchema(
                database_name=settings.db_name,
                table_name=settings.db_table,
                columns=tuple(columns),
            )
        except Error as exc:
            logger.exception("Failed to load live schema: %s", exc)
            raise RuntimeError("Failed to load schema metadata from MySQL.") from exc
        except RuntimeError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("Unexpected schema metadata error: %s", exc)
            raise RuntimeError("Unexpected schema metadata error.") from exc
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:  # pragma: no cover
                    logger.debug("Cursor close failed", exc_info=True)
            close_connection(connection)

    @staticmethod
    def _load_sample_values(cursor, column_name: str, data_type: str) -> tuple[str, ...]:
        if data_type not in _TEXT_TYPES:
            return ()
        try:
            cursor.execute(
                f"""
                SELECT DISTINCT `{column_name}` AS value
                FROM `{settings.db_table}`
                WHERE `{column_name}` IS NOT NULL AND TRIM(`{column_name}`) <> ''
                ORDER BY `{column_name}`
                LIMIT 8
                """,
            )
            rows = cursor.fetchall()
        except Error:
            logger.debug("Could not load sample values for %s", column_name, exc_info=True)
            return ()

        samples = []
        for row in rows:
            value = _row_value(row, "value", "VALUE", index=0)
            if value is None:
                continue
            samples.append(str(value).strip())
        return tuple(samples)
