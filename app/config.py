import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class CandidateProfile(BaseModel):
    reg_no: str = "21BCE1001"
    name: str = "Candidate Name"
    college_email: str = "candidate@college.edu"
    personal_email: str = "candidate@gmail.com"
    branch: str = "Computer Science and Engineering"
    degree: str = "B.Tech"
    cgpa: float = 8.5
    tenth_pct: float = 90.0
    twelfth_pct: float = 90.0
    active_backlogs: int = 0
    history_of_backlogs: int = 0
    career_interests: List[str] = Field(default_factory=lambda: [
        "Software Engineering",
        "Backend Development",
        "Machine Learning / AI",
        "Cloud Systems"
    ])
    disliked_roles: List[str] = Field(default_factory=lambda: [
        "Sales / Business Development",
        "Technical Support / Operations"
    ])
    red_flags: List[str] = Field(default_factory=lambda: [
        "Vellore campus",
        "offline interview at Vellore",
        "service agreement",
        "employment bond"
    ])
    min_ctc_lpa: float = 6.0
    preferred_locations: List[str] = Field(default_factory=lambda: [
        "Bengaluru", "Hyderabad", "Pune", "Remote"
    ])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Server Configuration
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Ingestion & Security
    COLLEGE_WEBHOOK_API_KEY: str = "dev_college_secret_key_12345"
    TELEGRAM_WEBHOOK_SECRET: str = "dev_telegram_secret_token_12345"

    # Telegram Bot
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    APP_BASE_URL: str = "http://localhost:8000"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./placement_agent.db"

    # LLM Settings
    GEMINI_API_KEY: str = ""
    GEMINI_DEEP_MODEL: str = "gemini-3.5-flash-lite"
    GEMINI_FAST_MODEL: str = "gemini-3.5-flash-lite"

    # Fallback LLM (Groq)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # Google APIs (Personal Gmail & Calendar OAuth)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REFRESH_TOKEN: str = ""
    PERSONAL_GMAIL_ADDRESS: str = ""

    # Google APIs (College Gmail OAuth — vitapstudent.ac.in)
    COLLEGE_GOOGLE_REFRESH_TOKEN: str = ""
    COLLEGE_GMAIL_ADDRESS: str = ""

    # Paths & Timezone
    PROFILE_PATH: str = "config/profile.json"
    TIMEZONE: str = "Asia/Kolkata"


settings = Settings()


def load_candidate_profile() -> CandidateProfile:
    """Loads the candidate profile from disk or defaults."""
    profile_path = Path(settings.PROFILE_PATH)
    if not profile_path.is_absolute():
        # resolve relative to project root
        root_dir = Path(__file__).resolve().parent.parent
        profile_path = root_dir / settings.PROFILE_PATH

    if profile_path.exists():
        try:
            with open(profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return CandidateProfile(**data)
        except Exception as e:
            logger.warning(f"Failed to parse profile JSON at {profile_path}: {e}. Using defaults.")
    else:
        logger.warning(f"Profile JSON not found at {profile_path}. Using default profile.")

    return CandidateProfile()
