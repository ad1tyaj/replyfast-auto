from dotenv import load_dotenv
import os

load_dotenv()

FLASK_ENV = os.getenv("FLASK_ENV", "production")
PORT = int(os.getenv("PORT", 5000))

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))

# Meta WhatsApp Business API Configuration
META_API_TOKEN = os.getenv("META_API_TOKEN")
META_PHONE_ID = os.getenv("META_PHONE_ID")

# Generic BSP Configuration (for future providers)
BSP_PROVIDER = os.getenv("BSP_PROVIDER", "meta")
BSP_API_KEY = os.getenv("BSP_API_KEY")
BSP_API_SECRET = os.getenv("BSP_API_SECRET")
BSP_CHANNEL_ID = os.getenv("BSP_CHANNEL_ID")

# Google Sheets Integration Configuration
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL")
SHEET_KEY = os.getenv("SHEET_KEY")  # For Apps Script deployment ID
SHEET_ID = os.getenv("SHEET_ID", "1BVHu5nW2uGbvLNDP4yATWQSiaBdgL9loY4WVIoC5e0Y")  # Your actual sheet ID
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "replyfast1-6c01977a46d4.json")
SHEETS_API_MODE = os.getenv("SHEETS_API_MODE", "direct")  # "direct" for API, "webhook" for Apps Script

# Webhook verification token
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "default_verify_token_change_this")

# Dealer/Admin Notification
DEALER_PHONE_NUMBER = os.getenv("DEALER_PHONE_NUMBER")  # WhatsApp number to send callback requests to
DEALER_NAME = os.getenv("DEALER_NAME", "Sales Team")  # Name of your dealership

# Appointment Scheduler
APPOINTMENT_REMINDER_INTERVAL = int(os.getenv("APPOINTMENT_REMINDER_INTERVAL", 300))  # 5 min default




