import socketio
import requests
import hmac
import hashlib
import time
import os
from PIL import Image

sio = socketio.Client()

# Ensure this matches the API_KEY set in your server's environment
API_KEY = os.environ.get("API_KEY", "test_secret_key").encode()
SERVER_URL = "http://localhost:5000"

# --- DEFINE YOUR FAKE SENSOR DATA HERE ---
# Change these values to simulate different plant conditions (e.g., cold, dry, ideal)
MOCK_SENSORS = {
    "need_watering": True,       # Simulating thirsty plant
    "low_humidity": False,
    "low_temp": True,            # Simulating cold environment
    "high_temp": False,
    "soil_moisture": 2500.0,
    "air_moisture": 45.0,
    "temp": 13.5,
    "lux": 150.0,
    "shadow_time": True,
    "sun_time": False,
    "spray_status": False,
    "mood": "sad"
}

@sio.event
def connect():
    print("Mock Plant successfully connected to the server!")
    sio.emit("send_weather")

@sio.on("mood")
def on_mood_request():
    """Triggered when a user sends a message. Sends the fake telemetry data."""
    print("\n[Server requested plant state] Sending mock telemetry data...")
    sio.emit("sensors_data", {"sensor": MOCK_SENSORS})

@sio.on("new_art_available")
def on_new_art(data):
    """Triggered when AI 1 finishes generating the image cluster payload."""
    print("\n" + "="*70)
    print("🎨 [ART PIPELINE] La pianta ha sognato una nuova opera!")
    print("-" * 70)
    print(f"🖌️  Stile/Medium : {data.get('medium', 'N/A')}")
    print(f"🖼️  Soggetto     : {data.get('canvas', 'N/A')}")
    print(f"🧠  Mappatura    : {data.get('mapping', 'N/A')}")
    print(f"💬  Spiegazione  : {data.get('explanation', 'N/A')}")
    print("="*70 + "\n")
    
    print("[Signal Received] Downloading new artwork...")
    try:
        res = requests.get(f"{SERVER_URL}/get_art", timeout=30)
        if res.status_code == 200:
            with open("simulated_art.png", "wb") as f:
                f.write(res.content)
            print("Artwork saved as 'simulated_art.png'. Opening visual frame...")
        else:
            print(f"Failed to fetch art from server. Status: {res.status_code}")
    except Exception as e:
        print(f"Error downloading image asset: {e}")

@sio.on("response")
def on_response(data):
    print(f"[Dialogue Engine Response]: {data}")

def connect_to_server():
    while True:
        try:
            nonce = str(int(time.time()))
            signature = hmac.new(API_KEY, nonce.encode(), hashlib.sha256).hexdigest()
            sio.connect(SERVER_URL, headers={"Authorization": f"{nonce}:{signature}"})
            break
        except Exception:
            print("Waiting for local server to start... Retrying in 5 seconds.")
            time.sleep(5)
    sio.wait()

if __name__ == "__main__":
    connect_to_server()