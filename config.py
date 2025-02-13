import os
from dotenv import load_dotenv
from typing import Optional

# Load environment variables from .env file
load_dotenv()

def get_env_var(var_name: str, default: Optional[str] = None, required: bool = True) -> str:
    """Get environment variable with validation."""
    value = os.getenv(var_name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {var_name}")
    return value

# Bot Tokens (required)
DA_BOT_TOKEN = get_env_var('DA_BOT_TOKEN')
SUPERVISOR_BOT_TOKEN = get_env_var('SUPERVISOR_BOT_TOKEN')
CLIENT_BOT_TOKEN = get_env_var('CLIENT_BOT_TOKEN')

# Cloudinary Configuration (required)
CLOUDINARY_CLOUD_NAME = get_env_var('CLOUDINARY_CLOUD_NAME')
CLOUDINARY_API_KEY = get_env_var('CLOUDINARY_API_KEY')
CLOUDINARY_API_SECRET = get_env_var('CLOUDINARY_API_SECRET')

# PostgreSQL Database Configuration
DB_HOST = get_env_var('DB_HOST', 'localhost', required=False)
DB_PORT = get_env_var('DB_PORT', '5432', required=False)
DB_NAME = get_env_var('DB_NAME', 'ftbot_db', required=False)
DB_USER = get_env_var('DB_USER', 'ahmedbeshir', required=False)
DB_PASSWORD = get_env_var('DB_PASSWORD', '', required=False)

# Construct Database URL
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Optional: Supervisor chat ID for notifications
SUPERVISOR_CHAT_ID = get_env_var('SUPERVISOR_CHAT_ID', required=False)
