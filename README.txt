JUUGTAPS BOT FOR RENDER

Files:
- bot.py
- requirements.txt

Render:
1. Create a GitHub repository and upload these 2 files.
2. Render -> New -> Web Service -> choose the repository.
3. Build Command: pip install -r requirements.txt
4. Start Command: python bot.py
5. Plan: Free (for testing)
6. Environment Variables:
   BOT_TOKEN = your Telegram bot token
   WEB_APP_URL = your game URL, e.g. https://example.github.io/your-repo/index.html
   FIREBASE_DATABASE_URL = https://juugtaaap-default-rtdb.europe-west1.firebasedatabase.app
   ADMIN_CHAT_ID = 5291965471

The bot uses Telegram polling and also opens a tiny HTTP health endpoint on Render's PORT.
