# langchain_module/logger.py
"""Module to configure centralized logging for the application."""
import logging
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG for more detailed logs
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "app.log")),
        logging.StreamHandler()  # Logs to console
    ]
)

def get_logger(name: str):
    """Create and return a logger instance for the specified module name."""
    return logging.getLogger(name)
