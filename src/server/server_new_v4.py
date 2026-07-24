import os, logging, os, secrets, threading, time, argparse, firebase_admin # type: ignore
from flask import Flask, request # type: ignore
from flask_socketio import SocketIO # type: ignore
from dotenv import load_dotenv # type: ignore
from datetime import datetime
from openai import OpenAI # type: ignore
from firebase_admin import credentials, firestore # type: ignore

# Imports from modules
from modules import globals, socket_events
from modules.tools import is_day, fetch_weather, weather_worker, check_thresholds, initialize_thresholds, initialize_keys, auto_clear
from modules.routes import register_routes
from modules.user_manager import update_user # type: ignore
from modules.classes import Thresholds, NeedKeys # type: ignore
from modules.chat_controller import register_chat_endpoint # type: ignore
from modules.ai_pipeline import initialize_llm

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

socket_events.register_socket_events(socketio, db, BASE_DIR, KEYS, API_KEY)

register_routes(
    app, 
    BASE_DIR, 
    db, 
    socketio,  
    lambda choice: initialize_llm(choice, socketio, BASE_DIR),
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
    BASE_DIR, 
    is_day
)

if __name__ == '__main__':
    global personality_check

    random_requests = False

    personality_check = "Server"
    initialize_thresholds(BASE_DIR, THRESHOLDS)

    initialize_keys(BASE_DIR, KEYS)

    socketio.start_background_task(target=check_thresholds, BASE_DIR=BASE_DIR, globals_obj=globals, socketio_obj=socketio, initialize_thresholds_func=initialize_thresholds, THRESHOLDS=THRESHOLDS)

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