from app.services.formatter import format_answer


def test_format_trim():
    result = format_answer("  hello world ")
    assert result == "hello world"


def test_format_empty():
    result = format_answer("")
    assert result == "No answer generated."