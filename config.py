import os
from dotenv import load_dotenv

load_dotenv()

# API Key Configuration
API_KEYS_COLLECTION = "api_keys"
API_KEY_EXPIRY_HOURS = 3
API_KEY_AUTO_ROTATE_HOURS = 2.5
API_KEY_SCOPES = [
    "whatsapp_marketing", "device_management"
]

# MongoDB Configuration
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://aimsghub_db_user:aZ4UspOceJSN2NBx@cluster0.5ufyyk6.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
DATABASE_NAME = "sender_pro"

# Environment variables
SENDGRID_MASTER_KEY = os.getenv("SENDGRID_MASTER_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
META_API_VERIFY_TOKEN = os.getenv("META_API_VERIFY_TOKEN", "your_webhook_verify_token")
SECRET_KEY = os.getenv("SECRET_KEY", "a_very_secret_key_for_jwt")
META_APP_ID = os.getenv("META_APP_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")
META_REDIRECT_URI = os.getenv("META_REDIRECT_URI")

# Application Configuration
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 180
REFRESH_TOKEN_EXPIRE_DAYS = 10
VECTOR_STORE_DIR = "user_vector_stores"
WHATSAPP_API_URL = "https://graph.facebook.com/v19.0"
SG_BASE = "https://api.sendgrid.com/v3"
BATCH_SIZE = 16

# Collections
USERS_COLLECTION = "users"
CAMPAIGNS_COLLECTION = "campaigns"
MESSAGE_STATUS_COLLECTION = "message_statuses"
CHAT_HISTORY_COLLECTION = "chat_history"
EMAIL_USERS_COLLECTION = "email_users"
EMAIL_LOGS_COLLECTION = "email_logs"
SMS_USERS_COLLECTION = "sms_users"
SMS_LOGS_COLLECTION = "sms_logs"

# Initialize clients
import sendgrid # pyright: ignore[reportMissingImports]
from twilio.rest import Client

sg_client = sendgrid.SendGridAPIClient(SENDGRID_MASTER_KEY) if SENDGRID_MASTER_KEY else None
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None