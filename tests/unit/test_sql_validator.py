"""Unit tests for the SQL Validator."""
from app.services.sql_validator import validate_sql

def test_validate_sql_valid():
    assert validate_sql("SELECT * FROM hotel_reservations") is True

def test_validate_sql_invalid_operation():
    assert validate_sql("UPDATE hotel_reservations SET price = 100") is False
    assert validate_sql("INSERT INTO hotel_reservations (id) VALUES (1)") is False

def test_validate_sql_wrong_table():
    assert validate_sql("SELECT * FROM users") is False

def test_validate_sql_forbidden_keyword_injection():
    # Blocks queries trying to sneak in a DROP statement
    assert validate_sql("SELECT * FROM hotel_reservations; DROP TABLE users;") is False