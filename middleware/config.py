from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv()


class Settings(BaseSettings):
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "reboot_dev"
    openai_api_key: str = ""
    reboot_llm_provider: str = "openai"
    sqlite_path: str = str(Path(__file__).parent / "feedback.db")
    server_port: int = 8000


settings = Settings()
