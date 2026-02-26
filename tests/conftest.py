import os

# Prevent bot from starting during tests
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "-1001234567890")
os.environ.setdefault("TELEGRAM_ADMIN_ID", "123456789")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
