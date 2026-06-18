# tests/conftest.py
import os
import pytest

# 1. Inject dummy environment variables BEFORE importing the app
os.environ["DB_HOST"] = "fake_host"
os.environ["DB_PORT"] = "3306"
os.environ["DB_USER"] = "test_user"
os.environ["DB_PASSWORD"] = "fake_password"
os.environ["DB_NAME"] = "test_db"
os.environ["DB_TABLE"] = "hotel_reservations"
os.environ["HF_API_KEY"] = "hf_dummy_test_key_12345"

from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    """FastAPI test client"""
    return TestClient(app)