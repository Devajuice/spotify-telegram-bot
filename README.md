# 🎵 Spotify Playlist Tracker - Telegram Bot

Track changes to your Spotify playlists and get notified in Telegram!

## ✨ Features

- 🔄 Automatic tracking every 2 minutes
- ➕ Notifications when songs are added
- ➖ Notifications when songs are removed
- 💾 Remembers changes even when bot restarts
- 🎨 Beautiful messages with album art
- 👥 Works in private chats and groups

## 🚀 Setup

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Create `.env` file with your credentials

3. Authenticate with Spotify:
4. Run `python spotify_auth.py` to authenticate with Spotify
5. Run `python telegram_bot.py` to start the bot

## 📋 Commands

- `/start` - Welcome message
- `/help` - Show all commands
- `/setplaylist <url>` - Set playlist to track
- `/status` - Check current status
- `/forcecheck` - Manual check
- `/stop` - Stop tracking

## 📝 License

Made with ❤️ for music lovers

