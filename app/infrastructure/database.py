# app/infrastructure/database.py
from sqlalchemy import create_engine
from app.core.settings import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

class DatabaseManager:
    """Centralized SQLAlchemy connection pool manager."""
    
    def __init__(self):
        # Using the centralized settings object instead of os.getenv!
        self.database_uri = (
            f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
        )
        self._engine = None

    @property
    def engine(self):
        """Returns a singleton SQLAlchemy engine with connection pooling."""
        if self._engine is None:
            logger.info(f"Initializing database connection pool for {settings.db_host}:{settings.db_port}/{settings.db_name}")
            self._engine = create_engine(
                self.database_uri,
                pool_size=settings.db_pool_size, # Also using settings for pool size
                max_overflow=10,      # Allow up to 10 extra connections during traffic spikes
                pool_recycle=1800,    # Recycle connections every 30 mins to prevent timeouts
                pool_pre_ping=True    # Check if connection is alive before using it
            )
        return self._engine

# Export a single shared instance
db_manager = DatabaseManager()