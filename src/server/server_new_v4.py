import os, json, hashlib, logging, os, secrets, re, hmac, threading, time, argparse, random, firebase_admin # type: ignore
from flask import Flask, render_template, request, jsonify, session # type: ignore
from flask_socketio import SocketIO, disconnect # type: ignore
from dotenv import load_dotenv # type: ignore
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from openai import OpenAI # type: ignore
from firebase_admin import credentials, firestore # type: ignore

# Imports from modules
from modules import ai_pipeline, globals
from modules.affective_engine import analyze_user_sentiment, update_plant_mood
from modules.tools import is_day, fetch_weather, flower_state, weather_worker, check_thresholds
from modules.routes import register_routes
from modules.user_manager import name_check, update_user, user_info # type: ignore
from modules.tools import send_flower_need, initialize_thresholds, initialize_keys, auto_clear
from modules.classes import Thresholds, NeedKeys # type: ignore
from modules.chat_controller import register_chat_endpoint # type: ignore

load_dotenv()  # Load environment variables from .env file

parser = argparse.ArgumentParser()
parser.add_argument("--request", choices=["yes", "no"], default="no")
args = parser.parse_args()
random_requests = True if args.request == "yes" else False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

template_dir = os.path.join(BASE_DIR, 'templates')
static_dir = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

# Initialize Firebase
json_filename = os.getenv("FIREBASE_JSON_NAME")
if not json_filename:
    raise ValueError("FIREBASE_JSON_NAME not set in .env")
cred_path = os.path.join(BASE_DIR, json_filename)
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Initialize Socket.IO
socketio = SocketIO(app)

# HMAC API key for Raspberry Pi authentication (from environment)
API_KEY = os.environ.get("API_KEY").encode()

# Configure logging to file
logging.basicConfig(filename=os.path.join(BASE_DIR, 'access.log'), level=logging.INFO)

# Initialize LLM.
llm_api_key = os.getenv("LLM_API_KEY")
client = OpenAI(api_key=llm_api_key, base_url="https://api.groq.com/openai/v1")
llm_model_name = "llama-3.1-8b-instant"

# Flask session secret
app.secret_key = secrets.token_hex(16)

# Current active web session id (only one user at a time)
auto_logout = 600  # Auto logout timeout in seconds

THRESHOLDS = Thresholds()
KEYS = NeedKeys()

@app.before_request
def log_request():
    """Log each incoming HTTP request to access.log."""
    now = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    logging.info(
        f"{now} - {request.remote_addr} - {request.method} {request.path}")

@app.after_request
def add_header(response):
    """
    Adds headers to prevent browser caching.
    """
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response  

@socketio.on("connect")
def handle_connect():
    """
    Socket.IO handler: Accetta Raspberry Pi e Browser Web.
    Salva il SID univoco del Raspberry per non confonderlo con il browser.
    """

    auth = request.headers.get("Authorization")

    if auth:
        try:
            nonce, signature = auth.split(":", 1)
        except ValueError:
            print("-> Invalid Authorization header")
            return disconnect()

        expected_signature = hmac.new(API_KEY, nonce.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            print("-> Invalid API Key")
            return disconnect()

        globals.rsb_connected = True
        globals.rsb_sid = request.sid  # Salva l'ID univoco della sessione del Raspberry
        print(f"-> Raspberry Pi connected (SID: {globals.rsb_sid})")

        if globals.angry:
            socketio.emit("angry_mode")
        elif globals.sad:
            socketio.emit("sad_mode")
        else:
            socketio.emit("reset_mood")
            
    else:
        print(f"-> Web Browser connected (SID: {request.sid})")

@socketio.on("disconnect")
def handle_disconnect():
    """Socket.IO handler: Verifica CHI si è disconnesso."""

    # Se a disconnettersi è stato il Raspberry...
    if request.sid == globals.rsb_sid:
        globals.rsb_connected = False
        globals.rsb_sid = None
        print("-> Raspberry Pi disconnected")
    # Se a disconnettersi è stato il browser web...
    else:
        print("-> Web Browser disconnected")

@socketio.on("spray_status")
def refresh_spray_status(data):
    """Socket.IO handler: Update last spray status timestamp."""

    if data.get("spray_status") is not None:
        globals.spray_status = datetime.strptime(
            data.get("spray_status"), "%Y-%m-%d %H:%M:%S")
    else:
        globals.spray_status = None

@socketio.on("water_time")
def get_watered_time(data):
    """Socket.IO handler: Update last watered time timestamp."""

    if data.get("last_water") is not None:
        globals.watered_time = datetime.strptime(
            data.get("last_water"), "%Y-%m-%d %H:%M:%S")
    else:
        globals.watered_time = None

@socketio.on("send_weather")
def send_weather():
    """Socket.IO handler: Send current weather to Raspberry Pi."""
    if globals.current_weather is not None:
        socketio.emit("weather", globals.current_weather)
    else:
        socketio.emit("weather", globals.current_weather)


def send_log(llm_input, thought, llm_response, sensors):
    """Save a chat log entry to the 'chat' collection in Firestore."""
    try:
        if "User input:" in llm_input:
            user_input = llm_input.split("User input:")[1].strip()
        else:
            user_input = None

        chat = {
            "time": datetime.now(),
            "user_id": globals.active_session_id,
            "username": globals.user.get("name", "Unknown"), # <--- Sicuro!
            "llm_input": llm_input,
            "user_input": user_input,
            "thought": thought,
            "llm_response": llm_response,
            "temperature": sensors["sensor"]["temp"],
            "soil_moisture": sensors["sensor"]["soil_moisture"],
            "air_moisture": sensors["sensor"]["air_moisture"],
            "lux": sensors["sensor"]["lux"],
            "spray_status": sensors["sensor"]["spray_status"],
            "mood": sensors["sensor"]["mood"],
            "low_humidity": sensors["sensor"]["low_humidity"],
            "need_watering": sensors["sensor"]["need_watering"],
            "low_temp": sensors["sensor"]["low_temp"],
            "high_temp": sensors["sensor"]["high_temp"],
            "random_water": globals.random_watering,
            "random_spray": globals.random_spray,
            "personality": "angry" if globals.angry else "sad" if globals.sad else "happy",
            # --- NEW ART LOGGING FIELDS ---
            "art_medium": globals.current_medium,
            "art_canvas": globals.current_canvas,
            "art_mapping": globals.current_mapping,
            "art_explanation": globals.current_explanation
        }

        db.collection("chat").add(chat)

    except Exception as e:
        print("Error sending log: ", e)

def to_logout():
    """Automatic logout due to inactivity."""

    print("-> User to_logout")
    globals.sad = False
    globals.angry = False
    globals.smoothed_wellbeing = 1.0  # Reset the smoothed wellbeing on logout

    if globals.rsb_connected:
        socketio.emit("reset_mood")
        socketio.emit("clear_request")
        socketio.emit("log_out")

    globals.user_flag = False
    globals.random_watering = False
    globals.random_spray = False

def logout_countdown(seconds):
    """
    Background countdown before automatically logging out the user.
    """

    print(f"Logout countdown started ({seconds}s)")

    for _ in range(seconds):
        socketio.sleep(1)
        if globals.cancel_logout:
            print("Logout canceled before completion.")
            return

    print("Logout timer expired.")
    socketio.start_background_task(to_logout)

@socketio.on("send_need")
def check_flower_need(data):
    """
    Socket.IO handler: Raspberry reports that a new need is active.

    Logic:
      - If we don't already have a key for that need (in KEYS), create a new
        'flower_needs' document and store its id.
      - Otherwise, just send back the existing key.
      - Save KEYS to 'need_keys.json' so state survives server restarts.
    """
    try:
        need = data.get("need")

        if need == "water":
            if KEYS.water is None:
                key = send_flower_need(need, db, socketio, BASE_DIR)
                KEYS.water = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.water, "need": need})
        elif need == "low_humidity":
            if KEYS.spray is None:
                air_moisture = data.get("air_moisture")
                key = send_flower_need(need, db, socketio, BASE_DIR, air_moisture)
                KEYS.spray = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.spray, "need": need})
        elif need == "cold":
            if KEYS.cold is None:
                temp = data.get("temp")
                key = send_flower_need(need, db, socketio, BASE_DIR, temp)
                KEYS.cold = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.cold, "need": need})
        elif need == "hot":
            if KEYS.hot is None:
                temp = data.get("temp")
                key = send_flower_need(need, db, socketio, BASE_DIR, temp)
                KEYS.hot = key
            else:
                socketio.emit("need_key", {"need_key": KEYS.hot, "need": need})
        elif need == "low_light":
            if KEYS.low_light is None:
                light = data.get("light")
                key = send_flower_need(need, db, socketio, BASE_DIR, light)
                KEYS.low_light = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.low_light, "need": need})
        elif need == "high_light":
            if KEYS.high_light is None:
                light = data.get("light")
                key = send_flower_need(need, db, socketio, BASE_DIR, light)
                KEYS.high_light = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.high_light, "need": need})

        # Save KEYS to 'need_keys.json'
        config_path = os.path.join(BASE_DIR, 'config', 'need_keys.json')
        with open(config_path, 'w', encoding='utf-8') as file:
            data_to_write = asdict(KEYS)
            json.dump(data_to_write, file, indent=4,)

    except Exception as e:
        print("Error sending flower need")

@socketio.on("need_fulfilled")
def fulfilled_need(data):
    """
    Socket.IO handler: a previously reported need has been fulfilled.

    Updates the corresponding 'flower_needs' document:
      - sets 'fulfilled' timestamp,
      - optionally sets 'sprayed' flag for low_humidity,
      - stores which user was connected when it was fulfilled,
      - clears the key from KEYS.
    """

    try:
        key = data.get("key")

        if globals.user and datetime.now() - globals.last_activity < timedelta(minutes=3):
            id = globals.user["user_id"]
            globals.user["requests_fulfilled"] = True
        else:
            id = None

        get_need = db.collection("flower_needs").document(key)

        if KEYS.spray == key and data.get("spray") is not None:
            sprayed = data.get("spray")
            get_need.update({
                "fulfilled": datetime.now(),
                "sprayed": sprayed,
                "user connected on fulfillment": id
            })
        else:
            get_need.update({
                "fulfilled": datetime.now(),
                "user connected on fulfillment": id
            })

        # Clear corresponding key from KEYS
        if KEYS.water == key:
            KEYS.water = None
        elif KEYS.spray == key:
            KEYS.spray = None
        elif KEYS.cold == key:
            KEYS.cold = None
        elif KEYS.hot == key:
            KEYS.hot = None
        elif KEYS.low_light == key:
            KEYS.low_light = None
        elif KEYS.high_light == key:
            KEYS.high_light = None

        config_path = os.path.join(BASE_DIR, 'config', 'need_keys.json')
        with open(config_path, 'w', encoding='utf-8') as file:
            data_to_write = asdict(KEYS)
            json.dump(data_to_write, file, indent=4,)

    except Exception as e:
        print("Error on fulfilling flower need", e)

@socketio.on("request_completed")
def request_completed_received(data):
    """
    Socket.IO handler: Raspberry reports that a random request was completed
    (either watering or spray).
    """
    request_completed(data.get("request"))

def request_completed(request):
    """
    Mark the corresponding random 'requests' document as fulfilled
    and reset random_watering / random_spray flags.
    """

    if request == "water" and globals.random_watering and datetime.now() - globals.random_watering_time < timedelta(minutes=3):
        # Random watering request was fulfilled
        globals.random_watering = False
        globals.random_watering_time = None
        req = db.collection("requests").document(globals.request_key)
        globals.request_key = None
        req.update({
            "fulfilled": datetime.now()
        })
    elif request == "spray":
        # Random spray request was fulfilled
        req = db.collection("requests").document(globals.request_key)
        globals.request_key = None
        globals.random_spray_time = None
        globals.random_spray = False

        req.update({
            "fulfilled": datetime.now()
        })

@socketio.on("sensors_data")
def get_flower_status(data):
    """Socket.IO handler: receive latest sensor data from Raspberry."""
    globals.sensors_data = data

def initialize_llm(choice):
    """Initialize LLM chat history with selected personality mood."""

    # Set the boolean flags and emit to the hardware based on the choice
    if choice in ["Χαρούμενο", "Happy"]:
        globals.angry = False
        globals.sad = False
        socketio.emit("reset_mood")
    elif choice in ["Γκρινιάρικο", "Grumpy"]:
        globals.angry = True
        globals.sad = False
        socketio.emit("angry_mode")
    elif choice in ["Λυπημένο", "Sad"]:
        globals.angry = False
        globals.sad = True
        socketio.emit("sad_mode")

    # Load only your active system prompt
    with open("system_prompt/llm_prompt_v2.txt", 'r', encoding='utf-8') as file:
        llm_prompt = file.read()

    globals.chat_history = [{"role": "system", "content": llm_prompt}]

@socketio.on("get_thresholds")
def get_thresholds():
    """Socket.IO handler: Send current thresholds to Raspberry Pi."""

    with open("plant_thresholds.json", 'r', encoding='utf-8') as file:
        file_thresholds = json.load(file)

    if globals.change_threshold:
        file_thresholds["light_min"] = 100

    thresholds = {
        "soil_moisture_min": file_thresholds["soil_moisture_min"],
        "air_moisture_min": file_thresholds["air_moisture_min"],
        "temp_min": file_thresholds["temp_min"],
        "temp_max": file_thresholds["temp_max"],
        "light_min": file_thresholds["light_min"],
        "light_max": file_thresholds["light_max"],
        "shadow": file_thresholds["shadow"],
        "sun": file_thresholds["sun"],
        "light_normal": file_thresholds["light_normal"],
        "spray": file_thresholds["spray"],
        "text_threshold": file_thresholds["text_threshold"]
    }

    socketio.emit("thresholds", thresholds)

register_routes(
    app, 
    BASE_DIR, 
    db, 
    socketio,  
    initialize_llm, 
    auto_logout
)

register_chat_endpoint(
    app, 
    socketio, 
    client, 
    llm_model_name, 
    THRESHOLDS, 
    random_requests, 
    db, 
    update_user, 
    send_log, 
    BASE_DIR, 
    is_day
)

if __name__ == '__main__':
    global personality_check

    random_requests = False

    personality_check = "Server"
    initialize_thresholds(BASE_DIR, THRESHOLDS)

    initialize_keys(BASE_DIR, KEYS)

    socketio.start_background_task(target=check_thresholds, BASE_DIR=BASE_DIR, globals_obj=globals, socketio_obj=socketio, initialize_thresholds_func=initialize_thresholds)

    threading.Thread(target=weather_worker, args=(globals, fetch_weather), daemon=True).start()

    while globals.current_weather is None:
        time.sleep(1)

    socketio.start_background_task(target=auto_clear, socketio=socketio)

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False
    )