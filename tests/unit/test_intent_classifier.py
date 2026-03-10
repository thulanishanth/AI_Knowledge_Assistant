from app.services.intent_classifier import classify_intent


def test_sql_keyword():
    assert classify_intent("SELECT * FROM users") == "sql"


def test_sql_natural_question():
    result = classify_intent("How many users exist in database?")
    assert result == "sql"


def test_general_question():
    result = classify_intent("What is machine learning?")
    assert result == "general"