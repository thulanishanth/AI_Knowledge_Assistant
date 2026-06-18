from app.infrastructure.repositories.schema_repository import ColumnProfile, TableSchema
from app.services.query_orchestrator import QueryOrchestrator


def _build_schema() -> TableSchema:
    return TableSchema(
        database_name="hotel_ai",
        table_name="hotel_reservations",
        columns=(
            ColumnProfile("Booking_ID", "varchar", False, True, ("INN00001",)),
            ColumnProfile("avg_price_per_room", "decimal", False, False, ()),
            ColumnProfile("booking_status", "varchar", False, False, ("Canceled", "Not_Canceled")),
        ),
    )


def test_build_schema_response_for_column_count_question():
    result = QueryOrchestrator._build_schema_response(
        question="how many coloumns are present in the database?",
        schema=_build_schema(),
        session_id="sess_1",
    )

    assert result is not None
    assert result.presentation == {
        "kind": "metric",
        "title": "Column count",
        "value": "3",
    }
    assert "has 3 columns" in result.answer
