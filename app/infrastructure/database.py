import os
from sqlalchemy import create_engine
from dotenv import load_dotenv
from app.core.logging import get_logger

logger = get_logger(__name__)

# Load environment variables securely
load_dotenv()

class DatabaseManager:
    """Centralized SQLAlchemy connection pool manager."""
    
    def __init__(self):
        self.user = os.getenv("DB_USER", "root")
        self.password = os.getenv("DB_PASSWORD", "1234")
        self.host = os.getenv("DB_HOST", "localhost")
        self.port = os.getenv("DB_PORT", "3306")
        self.db_name = os.getenv("DB_NAME", "hotel_db")
        
        self.database_uri = f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.db_name}"
        self._engine = None

    @property
    def engine(self):
        """Returns a singleton SQLAlchemy engine with connection pooling."""
        if self._engine is None:
            logger.info(f"Initializing database connection pool for {self.host}:{self.port}/{self.db_name}")
            self._engine = create_engine(
                self.database_uri,
                pool_size=5,          # Keep 5 connections open and ready
                max_overflow=10,      # Allow up to 10 extra connections during traffic spikes
                pool_recycle=1800,    # Recycle connections every 30 mins to prevent timeouts
                pool_pre_ping=True    # Check if connection is alive before using it
            )
        return self._engine

# Export a single shared instance
db_manager = DatabaseManager()