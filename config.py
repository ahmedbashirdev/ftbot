# config.py
DA_BOT_TOKEN = "7626185609:AAHUiK_XLARKBxxJngLoLm_CSI8YNmFIuhc"
SUPERVISOR_BOT_TOKEN = "7299635384:AAF3h8I7jVguq1EeDPduP4D6HlIgVC2HMRI"
CLIENT_BOT_TOKEN = "7671295940:AAHrxd0IBQdN1usysMmFYqnC-qyQ6r-j2mQ"
import os
from dotenv import load_dotenv

load_dotenv()

# PostgreSQL Database Configuration
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_NAME = os.getenv('DB_NAME', 'ftbot_db')
DB_USER = os.getenv('DB_USER', 'ahmedbeshir')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


# Cloudinary credentials
CLOUDINARY_CLOUD_NAME = "drshoxifw"
CLOUDINARY_API_KEY = "227146751192197"
CLOUDINARY_API_SECRET = "xyeXhE2jNwnwT995FMBkhLBfrqM"

# (Optional) If you want to hard–code a supervisor chat ID for notifications, add it here:
# SUPERVISOR_CHAT_ID = 123456789
