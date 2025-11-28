import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from dotenv import load_dotenv

load_dotenv()

# Authenticate with Spotify
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id=os.getenv('SPOTIFY_CLIENT_ID'),
    client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
    redirect_uri='http://localhost:8888/callback',
    scope='playlist-read-private playlist-read-collaborative',
    cache_path='.spotify_cache'
))

# Test the authentication
try:
    user = sp.current_user()
    print(f"✅ Successfully authenticated as: {user['display_name']}")
    print("Token cached in .spotify_cache file")
except Exception as e:
    print(f"❌ Authentication failed: {e}")
