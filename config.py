"""
config.py — Bill Translator Configuration
==========================================
Centralized configuration for Flask app, upload settings, session
management, and security defaults.

Adapted from the Accountability Agent's config.py pattern.
"""

import os
import secrets

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    """Flask application configuration."""

    # Flask core
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

    # File uploads
    UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
    MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "10"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_SIZE_MB * 1024 * 1024
    ALLOWED_EXTENSIONS = {"txt", "pdf", "md"}

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == "production"

    # Anthropic
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    # Brave Search (optional, for fact-checking)
    BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
    BRAVE_MAX_RESULTS = 5

    # Output
    OUTPUT_DIR = os.path.join(BASE_DIR, "translated_legislation")
