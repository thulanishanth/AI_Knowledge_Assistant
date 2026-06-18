from app.infrastructure.repositories.schema_repository import _row_value


def test_row_value_supports_case_insensitive_dict_keys():
    row = {"COLUMN_NAME": "booking_id", "DATA_TYPE": "varchar"}

    assert _row_value(row, "column_name") == "booking_id"
    assert _row_value(row, "data_type") == "varchar"


def test_row_value_supports_tuple_index_fallback():
    row = ("booking_id", "varchar", "NO", 1)

    assert _row_value(row, "column_name", index=0) == "booking_id"
    assert _row_value(row, "data_type", index=1) == "varchar"
