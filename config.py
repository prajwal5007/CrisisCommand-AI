import os
from dotenv import load_dotenv

load_dotenv()


def env_first(*names, default=None):
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return default


class Config:
    SECRET_KEY = env_first("SECRET_KEY", default="change-me-in-production")

    # Supports both the project's MYSQL_* variables and Railway MySQL variables.
    MYSQL_HOST = env_first("MYSQL_HOST", "MYSQLHOST", default="127.0.0.1")
    MYSQL_PORT = int(env_first("MYSQL_PORT", "MYSQLPORT", default="3306"))
    MYSQL_USER = env_first("MYSQL_USER", "MYSQLUSER", default="root")
    MYSQL_PASSWORD = env_first("MYSQL_PASSWORD", "MYSQLPASSWORD", default="")
    MYSQL_DATABASE = env_first("MYSQL_DATABASE", "MYSQLDATABASE", default="crisiscommand")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # Demo credentials shown in the UI for the hackathon presentation.
    # Change these for any non-demo deployment.
    DEMO_ADMIN_EMAIL = os.getenv("DEMO_ADMIN_EMAIL", "admin@crisiscommand.local")
    DEMO_ADMIN_PASSWORD = os.getenv("DEMO_ADMIN_PASSWORD", "demo123")

    # Optional SMTP settings for real password-reset emails.
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "")
