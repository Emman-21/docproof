"""Application configuration loaded from environment variables.

NOTE: This file is intentionally seeded with a SECRET_KEY variable that is
used by the application but is NOT declared in .env.example.  DocProof's
config_env agent should detect this as a missing declaration and produce a
"fail" contract for that key.
"""
import os

# Declared in .env.example — should produce "pass" contracts
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
API_KEY: str = os.environ.get("API_KEY", "")
PORT: int = int(os.environ.get("PORT", "3000"))

# NOT declared in .env.example — should produce a "fail" contract
SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
