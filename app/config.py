from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database Configuration
    db_host: str
    db_user: str
    db_password: str
    db_name: str
    
    # AI / LLM Configuration
    hf_api_key: str | None = None
    hf_model: str | None = None

    # Application Settings
    # This automatically loads the variables from your .env file
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore" # Ignores extra variables in the .env file that aren't defined here
    )

# Instantiate the settings object to be imported across the application
settings = Settings()