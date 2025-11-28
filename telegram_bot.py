import asyncio
import threading
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler
import os
from dotenv import load_dotenv
import asyncio
from pymongo import MongoClient
from datetime import datetime
import json
import re
import warnings
from telegram.ext import JobQueue

# Keep service alive (Render specific)
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Content-Length', '15')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def do_HEAD(self):
        """Handle HEAD requests (UptimeRobot uses these)"""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Content-Length', '15')
        self.end_headers()
    
    def do_POST(self):
        """Handle POST requests"""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def do_OPTIONS(self):
        """Handle OPTIONS requests"""
        self.send_response(200)
        self.send_header('Allow', 'GET, HEAD, POST, OPTIONS')
        self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress logs

warnings.filterwarnings('ignore')
load_dotenv()

# MongoDB setup
MONGO_URI = os.getenv('MONGO_URI')
mongo_client = MongoClient(MONGO_URI)
db = mongo_client['spotify_tracker']
playlist_collection = db['playlist_state']
config_collection = db['bot_config']

print("✅ Connected to MongoDB")

# Custom cache handler
class EnvironmentCacheHandler(CacheHandler):
    def __init__(self):
        self.token_data = None
        token_env = os.getenv('SPOTIFY_TOKEN_DATA')
        if token_env:
            try:
                self.token_data = json.loads(token_env)
                print("✅ Loaded Spotify token from environment")
            except Exception as e:
                print(f"⚠️ Failed to parse token: {e}")
    
    def get_cached_token(self):
        return self.token_data
    
    def save_token_to_cache(self, token_info):
        self.token_data = token_info

# Initialize Spotify client
cache_handler = EnvironmentCacheHandler()
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv('SPOTIFY_CLIENT_ID'),
    client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
    redirect_uri='http://127.0.0.1:8888/callback',
    scope='playlist-read-private playlist-read-collaborative',
    cache_handler=cache_handler,
    open_browser=False
))

# Store tracked chats
tracked_chats = {}

# Helper functions
def extract_playlist_id(playlist_input):
    """Extract playlist ID from URL or return as-is if already an ID"""
    if 'open.spotify.com' in playlist_input or 'spotify.com' in playlist_input:
        match = re.search(r'playlist/([a-zA-Z0-9]+)', playlist_input)
        if match:
            return match.group(1)
    if re.match(r'^[a-zA-Z0-9]+$', playlist_input):
        return playlist_input
    return None

def get_chat_playlist_id(chat_id):
    """Get playlist ID for specific chat"""
    config = config_collection.find_one({
        'platform': 'telegram',
        'chat_id': chat_id,
        'setting': 'playlist_id'
    })
    return config['playlist_id'] if config else None

def save_chat_playlist_id(chat_id, playlist_id):
    """Save playlist ID for specific chat"""
    config_collection.update_one(
        {'platform': 'telegram', 'chat_id': chat_id, 'setting': 'playlist_id'},
        {
            '$set': {
                'platform': 'telegram',
                'chat_id': chat_id,
                'setting': 'playlist_id',
                'playlist_id': playlist_id,
                'updated_at': datetime.utcnow()
            }
        },
        upsert=True
    )

def get_playlist_tracks(playlist_id):
    """Fetch all tracks from a playlist"""
    results = sp.playlist_items(playlist_id)
    tracks = results['items']
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
    return tracks

def get_playlist_info(playlist_id):
    """Get playlist metadata"""
    return sp.playlist(playlist_id, fields='name,owner.display_name,external_urls,images,tracks.total')

def save_playlist_state(platform, chat_id, playlist_id, track_ids, track_data):
    """Save current playlist state to MongoDB"""
    playlist_collection.update_one(
        {'platform': platform, 'chat_id': chat_id, 'playlist_id': playlist_id},
        {
            '$set': {
                'platform': platform,
                'chat_id': chat_id,
                'playlist_id': playlist_id,
                'track_ids': list(track_ids),
                'track_data': track_data,
                'last_updated': datetime.utcnow()
            }
        },
        upsert=True
    )

def get_saved_playlist_state(platform, chat_id, playlist_id):
    """Get last saved playlist state from MongoDB"""
    state = playlist_collection.find_one({
        'platform': platform,
        'chat_id': chat_id,
        'playlist_id': playlist_id
    })
    if state:
        return set(state['track_ids']), state.get('track_data', {})
    return set(), {}

def format_song_message(track_info, action):
    """Format song info as Telegram message"""
    track = track_info['track']
    song_name = track['name']
    artists = ', '.join([artist['name'] for artist in track['artists']])
    album = track['album']['name']
    song_url = track['external_urls']['spotify']
    duration_ms = track['duration_ms']
    duration_min = duration_ms // 60000
    duration_sec = (duration_ms % 60000) // 1000
    
    emoji = "➕" if action == "added" else "➖"
    
    message = (
        f"{emoji} *Song {action.capitalize()}!*\n\n"
        f"🎵 *{song_name}*\n"
        f"👤 Artist: {artists}\n"
        f"💿 Album: {album}\n"
        f"⏱ Duration: {duration_min}:{duration_sec:02d}\n\n"
        f"[Listen on Spotify]({song_url})"
    )
    
    album_art = track['album']['images'][0]['url'] if track['album']['images'] else None
    
    return message, album_art

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    keyboard = [
        [InlineKeyboardButton("📖 Help", callback_data='help')],
        [InlineKeyboardButton("➕ Add Bot to Group", url=f"https://t.me/{context.bot.username}?startgroup=true")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        '🎵 *Welcome to Spotify Playlist Tracker!*\n\n'
        'I track changes to your Spotify playlists and notify you when songs are added or removed!\n\n'
        'Use /help to see all available commands.',
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = """
🎵 *Spotify Playlist Tracker - Commands*

*Setup Commands:*
/setplaylist <url> - Set Spotify playlist to track
• Accepts full URL or playlist ID
• Example: `/setplaylist https://open.spotify.com/playlist/...`

*Information Commands:*
/status - Check current tracking status
/help - Show this help message

*Utility Commands:*
/forcecheck - Manually check playlist now
/stop - Stop tracking in this chat

*Features:*
✨ Automatic tracking every 2 minutes
➕ Notifications when songs are added
➖ Notifications when songs are removed
💾 Remembers changes even when bot restarts
🎨 Beautiful messages with album art

*Quick Start:*
1️⃣ Use /setplaylist with your Spotify playlist URL
2️⃣ That's it! Changes will be posted here automatically

_Made with ❤️ for music lovers_
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def set_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the playlist to track"""
    chat_id = update.effective_chat.id
    
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a playlist URL or ID!\n\n"
            "Usage: `/setplaylist <url_or_id>`\n"
            "Example: `/setplaylist https://open.spotify.com/playlist/37i9dQZF1DX...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    playlist_input = context.args[0]
    playlist_id = extract_playlist_id(playlist_input)
    
    if not playlist_id:
        await update.message.reply_text(
            "❌ Invalid playlist URL or ID!\n"
            "Please provide either a Spotify playlist URL or playlist ID."
        )
        return
    
    processing_msg = await update.message.reply_text("🔄 Setting up playlist tracking...")
    
    try:
        playlist_info = get_playlist_info(playlist_id)
        save_chat_playlist_id(chat_id, playlist_id)
        
        current_tracks_list = get_playlist_tracks(playlist_id)
        current_tracks = {track['track']['id']: track for track in current_tracks_list if track['track']}
        current_track_ids = set(current_tracks.keys())
        
        if chat_id not in tracked_chats:
            tracked_chats[chat_id] = {}
        tracked_chats[chat_id]['playlist_id'] = playlist_id
        tracked_chats[chat_id]['previous_tracks'] = current_track_ids
        
        save_playlist_state('telegram', chat_id, playlist_id, current_track_ids, current_tracks)
        
        message = (
            f"✅ *Playlist Set Successfully!*\n\n"
            f"🎵 [{playlist_info['name']}]({playlist_info['external_urls']['spotify']})\n"
            f"👤 Owner: {playlist_info['owner']['display_name']}\n"
            f"📊 Total Tracks: {playlist_info['tracks']['total']}\n"
            f"✅ Tracking: Active\n\n"
            f"I'll notify you here when songs are added or removed!\n"
            f"Checking every 2 minutes."
        )
        
        await processing_msg.edit_text(message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
        print(f"✅ Telegram: Now tracking playlist {playlist_info['name']} for chat {chat_id}")
        
    except spotipy.exceptions.SpotifyException as e:
        if e.http_status == 404:
            await processing_msg.edit_text(
                "❌ Playlist not found!\n"
                "Make sure the playlist URL/ID is correct and the playlist is public."
            )
        else:
            await processing_msg.edit_text(f"❌ Spotify error: {e.msg}")
    except Exception as e:
        await processing_msg.edit_text(f"❌ Error: {str(e)}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot status"""
    chat_id = update.effective_chat.id
    playlist_id = get_chat_playlist_id(chat_id)
    
    if not playlist_id:
        await update.message.reply_text(
            "⚠️ *No playlist set!*\n\n"
            "Use /setplaylist to start tracking a playlist.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    try:
        playlist_info = get_playlist_info(playlist_id)
        track_count = len(tracked_chats.get(chat_id, {}).get('previous_tracks', set()))
        
        message = (
            f"🎵 *Playlist Tracker Status*\n\n"
            f"*Current Playlist:*\n"
            f"[{playlist_info['name']}]({playlist_info['external_urls']['spotify']})\n\n"
            f"📊 Total Songs: {track_count}\n"
            f"⏱ Check Interval: Every 2 minutes\n"
            f"✅ Status: Active"
        )
        
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=False)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def force_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger playlist check"""
    chat_id = update.effective_chat.id
    
    msg = await update.message.reply_text("🔄 Checking playlist for changes...")
    
    try:
        await check_playlist_for_chat(context.application, chat_id)
        await msg.edit_text("✅ Check complete! Any changes have been posted.")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def stop_tracking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop tracking in this chat"""
    chat_id = update.effective_chat.id
    
    if chat_id in tracked_chats:
        del tracked_chats[chat_id]
    
    config_collection.delete_one({
        'platform': 'telegram',
        'chat_id': chat_id,
        'setting': 'playlist_id'
    })
    
    await update.message.reply_text(
        "🛑 *Tracking Stopped*\n\n"
        "I'll no longer send updates to this chat.\n"
        "Use /setplaylist to start tracking again.",
        parse_mode=ParseMode.MARKDOWN
    )

# Background task
async def check_playlist_for_chat(application, chat_id):
    """Check playlist for a specific chat"""
    if chat_id not in tracked_chats:
        playlist_id = get_chat_playlist_id(chat_id)
        if not playlist_id:
            return
        
        saved_tracks, saved_data = get_saved_playlist_state('telegram', chat_id, playlist_id)
        tracked_chats[chat_id] = {
            'playlist_id': playlist_id,
            'previous_tracks': saved_tracks
        }
    
    playlist_id = tracked_chats[chat_id]['playlist_id']
    previous_tracks = tracked_chats[chat_id]['previous_tracks']
    
    try:
        current_tracks_list = get_playlist_tracks(playlist_id)
        current_tracks = {track['track']['id']: track for track in current_tracks_list if track['track']}
        current_track_ids = set(current_tracks.keys())
        
        added_ids = current_track_ids - previous_tracks
        removed_ids = previous_tracks - current_track_ids
        
        for track_id in added_ids:
            message, album_art = format_song_message(current_tracks[track_id], 'added')
            if album_art:
                await application.bot.send_photo(
                    chat_id=chat_id,
                    photo=album_art,
                    caption=message,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN
                )
        
        for track_id in removed_ids:
            try:
                track_data = sp.track(track_id)
                track_info = {'track': track_data}
                message, album_art = format_song_message(track_info, 'removed')
                if album_art:
                    await application.bot.send_photo(
                        chat_id=chat_id,
                        photo=album_art,
                        caption=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except:
                await application.bot.send_message(
                    chat_id=chat_id,
                    text="🗑️ A song was removed from the playlist"
                )
        
        tracked_chats[chat_id]['previous_tracks'] = current_track_ids
        save_playlist_state('telegram', chat_id, playlist_id, current_track_ids, current_tracks)
        
    except Exception as e:
        print(f"Error checking playlist for chat {chat_id}: {e}")

async def check_all_playlists(context: ContextTypes.DEFAULT_TYPE):
    """Check all tracked playlists"""
    all_configs = config_collection.find({
        'platform': 'telegram',
        'setting': 'playlist_id'
    })
    
    for config in all_configs:
        chat_id = config['chat_id']
        await check_playlist_for_chat(context.application, chat_id)

async def check_all_playlists(application):
    """Check all tracked playlists (runs every 2 minutes)"""
    all_configs = config_collection.find({
        'platform': 'telegram',
        'setting': 'playlist_id'
    })
    
    for config in all_configs:
        chat_id = config['chat_id']
        await check_playlist_for_chat(application, chat_id)

def main():
    """Start the bot"""
    import time
    import threading
    
    # Create application
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("setplaylist", set_playlist))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("forcecheck", force_check))
    application.add_handler(CommandHandler("stop", stop_tracking))
    
    # Initialize tracked chats from database
    print("📂 Loading tracked chats from database...")
    all_configs = config_collection.find({
        'platform': 'telegram',
        'setting': 'playlist_id'
    })
    
    for config in all_configs:
        chat_id = config['chat_id']
        playlist_id = config['playlist_id']
        
        saved_tracks, saved_data = get_saved_playlist_state('telegram', chat_id, playlist_id)
        
        tracked_chats[chat_id] = {
            'playlist_id': playlist_id,
            'previous_tracks': saved_tracks
        }
    
    print(f"✅ Loaded {len(tracked_chats)} tracked chats from database")
    
    # Background checker function
    def background_playlist_checker():
        """Check playlists in background thread"""
        time.sleep(10)  # Wait 10 seconds before first check
        
        while True:
            try:
                print("🔄 Checking all playlists...")
                
                # Create new event loop for this thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                # Run the check
                loop.run_until_complete(check_all_playlists(application))
                
                # Close the loop
                loop.close()
                
            except Exception as e:
                print(f"❌ Error in periodic check: {e}")
            
            # Wait 2 minutes before next check
            time.sleep(120)
    
    # Start background checker thread
    checker_thread = threading.Thread(target=background_playlist_checker, daemon=True)
    checker_thread.start()
    
    # Start bot
    print("🤖 Telegram bot started!")
    print("✅ Ready to track Spotify playlists")
    print("📱 Send /start to your bot to begin")
    print("⏰ Checking playlists every 2 minutes")
    
    # Run bot polling (blocks here)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
