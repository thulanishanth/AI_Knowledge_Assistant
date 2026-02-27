#app/database/connection.py
"""Database connection and session management."""
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

logger = logging.getLogger(__name__)

# 1. Build the secure connection string utilizing the restricted ai_chatbot user
# We enforce charset=utf8mb4 to match your database creation strategy.
DATABASE_URL = (
    f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
    f"@{settings.db_host}/{settings.db_name}?charset=utf8mb4"
)

# 2. Initialize the SQLAlchemy engine
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verifies the connection is alive before routing a query
    pool_recycle=3600    # Prevents stale connections by recycling them every hour
)

# 3. Create the Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. Create the declarative base that our models will inherit from
Base = declarative_base()

# 5. Dependency injection for FastAPI routes
def get_db():
    """Dependency to provide a database session for requests."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
