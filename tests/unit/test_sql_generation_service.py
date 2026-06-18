from types import SimpleNamespace

import pytest

from app.infrastructure.repositories.schema_repository import ColumnProfile, TableSchema
from app.security.sql_guard import SqlGuard
from app.services.sql_generation_service import SQLGenerationService


def _build_schema() -> TableSchema:
    return TableSchema(
        database_name="hotel_ai",
        table_name="hotel_reservations",
        columns=(
            ColumnProfile("Booking_ID", "varchar", False, True, ("INN00001",)),
            ColumnProfile("avg_price_per_room", "decimal", False, False, ()),
            ColumnProfile("booking_status", "varchar", False, False, ("Canceled", "Not_Canceled")),
            ColumnProfile("room_type_reserved", "varchar", False, False, ("Room_Type 1",)),
        ),
    )


def _build_service() -> SQLGenerationService:
    schema_service = SimpleNamespace(select_examples=lambda *args, **kwargs: "")
    return SQLGenerationService(schema_service, SqlGuard("hotel_reservations"))


@pytest.mark.asyncio
async def test_generate_sql_handles_grouped_average_without_llm(monkeypatch):
    def fail_llm(*args, **kwargs):
        raise AssertionError("LLM should not be called for grouped averages covered by heuristics.")

    monkeypatch.setattr("app.services.sql_generation_service.call_llm", fail_llm)

    result = await _build_service().generate_sql(
        "Average price by booking status",
        _build_schema(),
    )

    assert result.is_valid
    assert result.strategy == "rule_based"
    assert "AVG(`avg_price_per_room`)" in result.sql
    assert "GROUP BY `booking_status`" in result.sql


@pytest.mark.asyncio
async def test_generate_sql_keeps_group_column_distinct_from_measure(monkeypatch):
    def fail_llm(*args, **kwargs):
        raise AssertionError("LLM should not be called for grouped averages covered by heuristics.")

    monkeypatch.setattr("app.services.sql_generation_service.call_llm", fail_llm)

    result = await _build_service().generate_sql(
        "average avg price per room by booking status",
        _build_schema(),
    )

    assert result.is_valid
    assert result.strategy == "rule_based"
    assert "AVG(`avg_price_per_room`)" in result.sql
    assert "GROUP BY `booking_status`" in result.sql
    assert "GROUP BY `avg_price_per_room`" not in result.sql
