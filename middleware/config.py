from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# BaseSettings automatically takes the values of matching (case-insensitive) environment variables.
load_dotenv()


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "reboot_dev"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-mini"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    sqlite_path: str = str(Path(__file__).parent / "feedback.db")
    server_port: int = 8000
    # Lazy exponential decay: effective = stored * exp(-lambda * days_since_last_positive)
    confidence_decay_lambda: float = 0.05
    # Added to "now" when computing decay (demo: simulate 30 days passing without waiting)
    demo_time_offset_days: int = 0


settings = Settings()
