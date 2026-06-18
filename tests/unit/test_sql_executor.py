"""Unit tests for the SQL Executor."""
from app.services.sql_executor import execute_safe_query

def test_execute_safe_query_success(monkeypatch):
    class MockCursor:
        def execute(self, query): pass
        def fetchall(self): return [{"id": 1, "status": "confirmed"}]
        def close(self): pass

    class MockConnection:
        def is_connected(self): return True
        def cursor(self, dictionary=True): return MockCursor()
        def close(self): pass

    monkeypatch.setattr("app.services.sql_executor.create_db_connection", lambda: MockConnection())
    
    result = execute_safe_query("SELECT * FROM hotel_reservations")
    assert isinstance(result, list)
    assert result[0]["status"] == "confirmed"

def test_execute_safe_query_connection_fail(monkeypatch):
    class MockConnectionFail:
        def is_connected(self): return False
    
    monkeypatch.setattr("app.services.sql_executor.create_db_connection", lambda: MockConnectionFail())
    
    result = execute_safe_query("SELECT * FROM hotel_reservations")
    assert "Acquired connection is not connected" in result