from app.services.confidence_checker import check_confidence


def test_empty_answer():
    """
    Confidence should be 0.0 when answer is empty.
    """
    result = check_confidence("", "")
    assert result == 0.0


def test_error_answer():
    """
    Confidence should be 0.0 if answer starts with 'error'.
    """
    result = check_confidence("Error: something went wrong", "")
    assert result == 0.0


def test_sql_answer_high_confidence():
    """
    If answer contains SQL keywords, confidence should be 1.0.
    """
    answer = "SELECT COUNT(*) FROM hotel_reservations"
    result = check_confidence(answer, "")
    assert result == 1.0


def test_general_answer_medium_confidence():
    """
    Normal answers should return moderate confidence.
    """
    answer = "There are 25 confirmed bookings in the database."
    result = check_confidence(answer, "")
    assert result == 0.8