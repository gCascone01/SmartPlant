from flask import Flask, render_template, request, jsonify, session, send_file
from flask_socketio import SocketIO, disconnect
import json
from dataclasses import dataclass, asdict
from astral import LocationInfo
from astral.sun import sun
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import requests
import secrets
import pytz
import re
import hmac
import threading
import time
import argparse
from google.genai import types
from google.genai.errors import ServerError
import random
import firebase_admin
from firebase_admin import credentials, firestore
from google import genai
from openai import OpenAI
import json
import math

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

template_dir = os.path.join(BASE_DIR, 'templates')
static_dir = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

# Initialize Firebase
cred_path = os.path.join(BASE_DIR, "smartplant-f8c09-firebase-adminsdk-fbsvc-cdf41b5af3.json")
cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

# Initialize Socket.IO
socketio = SocketIO(app)

# HMAC API key for Raspberry Pi authentication (from environment)
API_KEY = os.environ.get("API_KEY").encode()

# Configure logging to file

logging.basicConfig(filename=os.path.join(BASE_DIR, 'access.log'), level=logging.INFO)

# lms.configure_default_client("150.140.142.84:1234")

# LLM client (Gemini).
client = OpenAI(base_url="http://150.140.142.84:1234/v1", api_key="lm-studio")
llm_model_name = "gemma-4-e4b-it"
chat_history = []

# Flask session secret
app.secret_key = secrets.token_hex(16)

# Current active web session id (only one user at a time)
active_session_id = None
last_activity = None  # Last time the active user interacted
auto_logout = 600  # Auto logout timeout in seconds

sensors_data = None  # Last sensor payload received from Raspberry Pi
rsb_connected = False  # True if Raspberry is currently connected via Socket.IO
thresholds_to_check = None  # Save thresholds to check for changes
current_weather = None  # Current weather condition
change_threshold = False  # Flag used when thresholds are being edited

angry = False  # True if flower is in angry mood
sad = False  # True if flower is in sad mood
smoothed_valence = 1.0   # Global coordinate for Valence (EMA)
smoothed_arousal = 0.0   # Global coordinate for Arousal (EMA)

user = None  # Current user informantions
spray_status = None  # Last spray timestamp (datetime)
watered_time = None  # Last watering timestamp (datetime)

random_watering = False  # Whether random watering request is enabled
random_watering_time = None  # Time of last random watering request
random_spray = False  # Whether random spray request is enabled
random_spray_time = None  # Time of last random spray request

request_key = None  # Firestore key for the current random request
new_user = False  # True if this is the first visit of the user
cancel_logout = False  # Flag to cancel pending auto-logout if there's interaction
user_flag = False  # True if user data has been loaded

# Global variables for Art Pipeline
current_explanation = "No painting generated yet."
current_image_prompt = ""
current_medium = ""           # NEW
current_canvas = ""           # NEW
current_mapping = ""          # NEW
previous_plant_state = None 


@dataclass
class Thresholds:
    """Thresholds for plant"""
    soil_moisture_min: float = None
    air_moisture_min: float = None
    temp_min: float = None
    temp_max: float = None
    light_min: float = None
    light_max: float = None


THRESHOLDS = Thresholds()


@dataclass
class NeedKeys:
    """Store Firestore keys for each active plant need entry."""
    water: str = None
    spray: str = None
    hot: str = None
    cold: str = None
    low_light: str = None
    high_light: str = None


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


# Variabile globale per tenere traccia dell'ID univoco del Raspberry
rsb_sid = None  

@socketio.on("connect")
def handle_connect():
    """
    Socket.IO handler: Accetta Raspberry Pi e Browser Web.
    Salva il SID univoco del Raspberry per non confonderlo con il browser.
    """
    global rsb_connected, rsb_sid

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

        rsb_connected = True
        rsb_sid = request.sid  # Salva l'ID univoco della sessione del Raspberry
        print(f"-> Raspberry Pi connected (SID: {rsb_sid})")

        if angry:
            socketio.emit("angry_mode")
        elif sad:
            socketio.emit("sad_mode")
        else:
            socketio.emit("reset_mood")
            
    else:
        print(f"-> Web Browser connected (SID: {request.sid})")


@socketio.on("disconnect")
def handle_disconnect():
    """Socket.IO handler: Verifica CHI si è disconnesso."""
    global rsb_connected, rsb_sid

    # Se a disconnettersi è stato il Raspberry...
    if request.sid == rsb_sid:
        rsb_connected = False
        rsb_sid = None
        print("-> Raspberry Pi disconnected")
    # Se a disconnettersi è stato il browser web...
    else:
        print("-> Web Browser disconnected")


@socketio.on("spray_status")
def refresh_spray_status(data):
    """Socket.IO handler: Update last spray status timestamp."""
    global spray_status

    if data.get("spray_status") is not None:
        spray_status = datetime.strptime(
            data.get("spray_status"), "%Y-%m-%d %H:%M:%S")
    else:
        spray_status = None


@socketio.on("water_time")
def get_watered_time(data):
    """Socket.IO handler: Update last watered time timestamp."""
    global watered_time

    if data.get("last_water") is not None:
        watered_time = datetime.strptime(
            data.get("last_water"), "%Y-%m-%d %H:%M:%S")
    else:
        watered_time = None


@app.route('/')
def chat_html():
    """Serve main chat HTML page."""
    return render_template("flower.html")


@app.route("/show_form", methods=['POST'])
def to_show_form():
    """Check if the user should be prompted to fill in the feedback form."""
    if user["messages"] >= 7 and not user["form_submitted"]:
        return jsonify(status="form")

    return jsonify(status="ok")


@app.route("/show_email", methods=['POST'])
def to_show_email():
    """Check if the user should be prompted to fill in their email."""

    if user["sessions"] >= 2:
        email = user.get("email")
        if not email:
            return jsonify(status="email")

    return jsonify(status="ok")


@app.route("/user_form", methods=['POST'])
def click_form_button():
    """Mark that the current user has submitted the feedback form."""
    try:
        user_info = db.collection("users").document(user["user_id"])

        user_info.update({
            "form_submitted": True,
            "form_submitted_time": datetime.now()
        })

        user["form_submitted"] = True

    except Exception as e:
        print("Error checking form: ", e)


@app.route("/check_user", methods=['POST'])
def check_user():
    """Check if a new user is trying to connect."""
    global active_session_id, last_activity, sad, angry, random_watering, random_spray, request_key, cancel_logout, user_flag

    connect_time = datetime.now()

    data = request.get_json()
    user_id = data.get("user_id")

    try:
        # Initialize session id if not set
        if "session_id" not in session:
            session["session_id"] = user_id

        # Auto-logout previous user if inactive for too long
        if active_session_id and \
                last_activity and \
                connect_time - last_activity > timedelta(seconds=auto_logout):
            print("-> Previous user Auto-Logout")
            sad = False
            angry = False
            active_session_id = None
            last_activity = None
            random_watering = False
            random_spray = False
            request_key = None
            socketio.emit("clear_request")

        # No active session -> this user becomes active
        if active_session_id is None:

            print("-> New user")
            socketio.emit("clear_request")
            active_session_id = session["session_id"]
            user_info(active_session_id)
            request_key = None
            last_activity = connect_time
            user_flag = True
            return jsonify(status="ok")

        # Same user returned -> refresh last_activity and cancel logout countdown
        if session['session_id'] == active_session_id:
            print("-> Same user returned")
            user_info(active_session_id, returned=True)
            cancel_logout = True
            last_activity = connect_time
            user_flag = True
            return jsonify(status="ok")
    except Exception as e:
        print("Error checking user: ", e)

    return jsonify(status="wait")


@app.route('/check_new_user', methods=['POST'])
def check_personality_selection():
    """Set a personality for the user."""
    global new_user

    try:
        if new_user:
            new_user = False
            return jsonify(user=True, mood=user["mood"], messages=user["messages"], session=user["sessions"])
        else:
            return jsonify(user=False, mood=user["mood"], messages=user["messages"], session=user["sessions"])
    except Exception as e:
        print("Error checking new user: ", e)


@app.route('/end', methods=['POST'])
def end():

    return jsonify(status="success")


@app.route('/message', methods=['POST'])
def flower_response():
    """Handle user message, process 2D circumplex affect vector, and update pipelines."""
    global chat, last_activity, active_session_id, sensors_data, user, random_watering, random_spray
    global current_explanation, current_image_prompt, previous_plant_state
    global angry, sad, smoothed_valence, smoothed_arousal

    if active_session_id is None or session['session_id'] != active_session_id:
        return jsonify(status="refresh")

    message = request.json['message']

    if not rsb_connected:
        return jsonify(status="no_connection")

    # Calculate temporal delta before updating last_activity timestamp
    now_time = datetime.now()
    if last_activity is not None:
        delta_time = (now_time - last_activity).total_seconds()
    else:
        delta_time = None

    last_activity = now_time
    socketio.emit("mood")

    try:
        data_ok = False
        timeout = 12
        while timeout > 0:
            socketio.sleep(0.1)
            if sensors_data is not None:
                sensors = sensors_data
                sensors_data = None
                data_ok = True
                break
            timeout -= 0.1
    except Exception as e:
        print("Error getting sensors: ", e)

    if not data_ok:
        return jsonify(status="error")

    # ==================== ADVANCED AFFECTIVE COMPUTING PIPELINE ====================
    # 1. Extract lexical sentiment polarity via Transformer
    user_sentiment_score = analyze_user_sentiment(message)
    
    # 2. Update continuous 2D coordinate systems and extract discrete mood label
    current_mood_label, V_instant, A_instant = update_plant_mood(sensors["sensor"], user_sentiment_score, delta_time)
    
    # 3. CONSOLE TELEMETRY LOGGING (Data Science Tracking)
    print("\n" + "="*70)
    print(f"[USER INPUT]      : '{message}'")
    print(f"[NLP POLARITY]    : Sentiment Score = {user_sentiment_score:.4f}")
    print(f"[TIME DELTA]      : {f'{delta_time:.2f}s' if delta_time else 'First message'}")
    print("-"*70)
    print(f"[VALENCE STAGE]   : Instant = {V_instant:.4f}  --->  Smoothed (EMA) = {smoothed_valence:.4f}")
    print(f"[AROUSAL STAGE]   : Instant = {A_instant:.4f}  --->  Smoothed (EMA) = {smoothed_arousal:.4f}")
    print("-"*70)
    print(f"[CIRCUMPLEX MOOD] : Active State = {current_mood_label}")
    print("="*70 + "\n")
    
    # 4. Sync physical hardware behaviors via Socket.IO
    if angry:
        socketio.emit("angry_mode")
    elif sad:
        socketio.emit("sad_mode")
    else:
        socketio.emit("reset_mood")
    # ===============================================================================

    global current_medium, current_canvas, current_mapping

    try:
        state_res = flower_state(sensors["sensor"])

        info = "<plant_state>\n"
        for status in state_res:
            info += status + "\n"

        user["messages"] += 1
        threading.Thread(target=update_user, args=(user,), daemon=True).start()

        info += f"- Weather: {current_weather if current_weather is not None else 'unknown'}.\n"
        info += f"- Current Emotional Mood: {current_mood_label}.\n"
        info += f"- Affect Coordinates: Valence={smoothed_valence:.2f}, Arousal={smoothed_arousal:.2f}.\n"
        info += "- Datetime: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ".\n"
        info += "- Is day: " + ("yes" if is_day() else "no") + ".\n"
        
        # FIX: Accesso sicuro al nome utente
        info += f"- The user's name is {user.get('name', 'Unknown')}.\n"
        info += "</plant_state>\n"

        is_revealing = request.json.get('is_revealing', False)
        
        if is_revealing:
            info += (
                "\n- CRITICAL LIVE CONTEXT: You are currently rendering and growing this new painting in real-time on the user's interface. "
                "The capillary tissues and canvas veins are still expanding. If the user asks questions about this specific artwork in creation, "
                "you can answer and provide information about its core conceptual meaning, but you MUST keep it very general, high-level, and abstract. "
                "Do not provide grand, microscopic, or highly specific visual details yet, as the painting is still physically forming on their screen. "
                "Acknowledge proudly that you are actively channeling your fluid dynamics into this ongoing biological growth process right now.\n"
            )

        # FIX: Passiamo lo stile e il soggetto alla pianta per dare contesto
        info += f"\n- VISUAL ELEMENTS: {current_image_prompt}. (Style: {current_medium}. Subject: {current_canvas}).\n"
        info += f"\n- PAINTING EXPLANATION: {current_explanation}\n"
        llm_input = info + "User input: " + message

        # ==================== CONDITIONED MULTIMODAL ART GENERATION (AI 1) ====================
        current_state = (state_res, current_weather, current_mood_label)

        if current_state != previous_plant_state:
            print("Plant affect vector shifted to a new quadrant! Commencing styled art pipeline...")
            
            reveal_duration = calculate_reveal_duration(sensors["sensor"])
            base_dir = os.path.dirname(os.path.abspath(__file__))
            prompt_path = os.path.join(BASE_DIR, "system_prompt", "new_llm_prompt.txt")
            
            with open(prompt_path, "r", encoding="utf-8") as file:
                art_system_instruction = file.read()

            # Mapping the 4 quadrants of Russell's Circumplex Model onto specific artistic constraints
            if current_mood_label == "Excited":
                mood_art_constraints = (
                    "- CURRENT ARTISTIC QUADRANT: High Valence, High Arousal (Excited/Energetic).\n"
                )
            elif current_mood_label == "Calm":
                mood_art_constraints = (
                    "- CURRENT ARTISTIC QUADRANT: High Valence, Low Arousal (Calm/Serene).\n"
                )
            elif current_mood_label == "Anxious":
                mood_art_constraints = (
                    "- CURRENT ARTISTIC QUADRANT: Low Valence, High Arousal (Tense/Turbulent).\n"
                )
            else:  # Lethargic
                mood_art_constraints = (
                    "- CURRENT ARTISTIC QUADRANT: Low Valence, Low Arousal (Lethargic/Dormant).\n"
                )

            art_input = info + f"\n{mood_art_constraints}\n- Your PREVIOUS artwork was: {current_explanation}\n- RULE: You MUST pivot to a completely different subject scenario but strictly adhere to these quadrant stylistic constraints."

            art_response = client.chat.completions.create(
                model=llm_model_name,
                messages=[
                    {"role": "system", "content": art_system_instruction},
                    {"role": "user", "content": art_input}
                ],
                temperature=0.95
            )

            # FIX: Pulizia stringa Markdown JSON e caricamento sicuro
            raw_art_content = art_response.choices[0].message.content
            cleaned_art_content = re.sub(r'^```json\s*|```$', '', raw_art_content.strip(), flags=re.MULTILINE).strip()
            
            try:
                art_data = json.loads(cleaned_art_content)
            except json.JSONDecodeError as e:
                print(f"Error JSON Parsing Art Response: {e}")
                art_data = {}

            current_medium = art_data.get("medium_and_style", "")
            current_canvas = art_data.get("random_canvas_subject", "")
            current_mapping = art_data.get("metaphorical_mapping", "")
            current_image_prompt = art_data.get("image_prompt", "")
            current_explanation = art_data.get("explanation", "")

            if current_image_prompt:
                threading.Thread(target=run_image_generation, args=(current_image_prompt, reveal_duration, current_medium, current_canvas, current_mapping, current_explanation), daemon=True).start()

            previous_plant_state = current_state

        else:
            print("Continuous space vector within safe quadrant boundaries. Skipping image generation.")
            
    except Exception as e:
        print(f"CRITICAL ERROR prima del Dialogue Engine: {e}")
    # ========================================================================================

    # ==================== DIALOGUE ENGINE (AI 2) ====================
    try:
        chat_history.append({"role": "user", "content": llm_input})

        for attempt in range(3):
            try:
                prediction_response = client.chat.completions.create(
                    model=llm_model_name,
                    messages=chat_history,
                    temperature=0.7
                )
                break
            except Exception as e:
                if attempt == 2: raise e
                print("Server busy, retrying in 2 seconds...")
                time.sleep(2)

        prediction = prediction_response.choices[0].message.content
        chat_history.append({"role": "assistant", "content": prediction})

        try:
            prediction_cleaned = re.sub(r'^```json\s*|```$', '', prediction.strip(), flags=re.MULTILINE).strip()
            prediction_dict = json.loads(prediction_cleaned)
        except json.JSONDecodeError as e:
            print(f"Error JSON Parsing: {e}")
            prediction_dict = prediction

        threading.Thread(target=send_log, args=(llm_input, None, prediction_cleaned, sensors, random_watering, random_spray, current_medium, current_canvas, current_mapping, current_explanation), daemon=True).start()

        if not rsb_connected:
            return jsonify(status="no_connection")

        socketio.emit("response", prediction_dict)
        return jsonify(status="success")
        
    except Exception as e:
        print(f"Error Server: {e}")
        socketio.emit("response", "Sorry, I cannot talk right now.")
        return jsonify(status="success")

def name_check(name):
    """
    Validate username: only Greek/Latin letters and underscore are allowed.
    """
    return bool(re.fullmatch(r"[A-Za-zΑ-Ωα-ωΆ-Ώά-ώ_]+", name))


def get_weather():
    """Background task that periodically fetches current weather."""
    global current_weather, change_threshold

    while True:
        try:
            city = "Agrinio"
            current_weather = requests.get(
                f"http://wttr.in/{city}?format=%C").text

            if current_weather is None:
                current_weather = "unknown"

            # If it's not sunny/clear, lower light thresholds
            if not any(word in current_weather.lower() for word in ["clear", "sun", "sunny", "bright"]):
                change_threshold = True
            else:
                change_threshold = False
            print(
                f"Weather updated: {current_weather}, change_threshold={change_threshold}")
        except Exception as e:
            current_weather = "unknown"
            print(f"Error on getting weather: {e}")

        time.sleep(1700)


@socketio.on("send_weather")
def send_weather():
    """Socket.IO handler: Send current weather to Raspberry Pi."""
    if current_weather is not None:
        socketio.emit("weather", current_weather)
    else:
        socketio.emit("weather", current_weather)


def send_log(llm_input, thought, llm_response, sensors, random_watering, random_spray, art_medium, art_canvas, art_mapping, art_explanation):
    """Save a chat log entry to the 'chat' collection in Firestore."""
    try:
        if "User input:" in llm_input:
            user_input = llm_input.split("User input:")[1].strip()
        else:
            user_input = None

        chat = {
            "time": datetime.now(),
            "user_id": active_session_id,
            "username": user.get("name", "Unknown"), # <--- Sicuro!
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
            "random_water": random_watering,
            "random_spray": random_spray,
            "personality": "angry" if angry else "sad" if sad else "happy",
            # --- NEW ART LOGGING FIELDS ---
            "art_medium": art_medium,
            "art_canvas": art_canvas,
            "art_mapping": art_mapping,
            "art_explanation": art_explanation
        }

        db.collection("chat").add(chat)

    except Exception as e:
        print("Error sending log: ", e)


def update_user(user):

    try:
        user_info = db.collection("users").document(user["user_id"])

        user_info.update({
            "messages": user["messages"],
            "random_requests": user["random_requests"],
        })

    except Exception as e:
        print("Error updating user: ", e)


@app.route("/spray_button", methods=["POST"])
def spray():
    """
    HTTP endpoint triggered when the user presses the spray button in the UI.
    """
    global random_spray, last_activity

    if not rsb_connected:
        return jsonify(status="no_connection")

    socketio.emit("spray")

    last_activity = datetime.now()

    if random_spray:
        # Complete the random spray request
        random_spray = False
        request_completed("spray")

    return jsonify(status="success")

@app.route("/force_art", methods=["POST"])
def force_art():
    """HTTP endpoint to force AI 1 to generate new art based on a completely random state."""
    global current_explanation, current_image_prompt, previous_plant_state, user
    
    if not rsb_connected:
        return jsonify(status="no_connection")

    # 1. Generate completely randomized physical telemetry
    water_states = ["- Watering: Do not need watering.", "- Watering: Yes, i need.", "- Watering: Yes, immediately!!"]
    light_states = ["- Light: Ideal.", "- Light: low sun exposure, need more sunlight !", "- Light: high sun exposure!!"]
    temp_states = ["- Temperature: Ideal.", "- Temperature: low temperature!", "- Temperature: high temperature!!"]
    hum_states = ["- Air humidity: Ideal.\n- Leaf spray: Do not need.", "- Air humidity: very low. Needs spraying water on leaves!"]
    weathers = ["Sunny", "Overcast", "Raining", "Severe Heatwave", "Snowing", "Clear night", "Thunderstorm"]
    
    state_res = (random.choice(water_states), random.choice(light_states), random.choice(temp_states), random.choice(hum_states))
    simulated_weather = random.choice(weathers)
    
    # 2. Build the <plant_state> string
    info = "<plant_state>\n"
    for status in state_res:
        info += status + "\n"
    info += f"- Weather: {simulated_weather}.\n"
    info += "- Datetime: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ".\n"
    info += f'- The user\'s name is {user["name"] if user else "Unknown"}.\n'
    info += "</plant_state>\n"

    # 3. Define the background generation task
    def run_forced_art(info_text, new_state):
        global current_explanation, current_image_prompt, previous_plant_state
        global current_medium, current_canvas, current_mapping
        
        print(f"Forcing new art generation with simulated weather: {new_state[1]}")
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            prompt_path = os.path.join(base_dir, "system_prompt", "new_llm_prompt.txt")
            with open(prompt_path, "r", encoding="utf-8") as file:
                art_system_instruction = file.read()

            art_input = info_text + f"\n- Your PREVIOUS artwork was: {current_explanation}\n- RULE: You MUST choose a completely different scenario and artistic style for this new generation."

            art_response = client.chat.completions.create(
                model=llm_model_name,
                messages=[
                    {"role": "system", "content": art_system_instruction},
                    {"role": "user", "content": art_input}
                ],
                temperature=0.85 # Slightly higher temperature to force extreme creative variance
            )

            # Pulizia JSON Markdown
            raw_art_content = art_response.choices[0].message.content
            cleaned_art_content = re.sub(r'^```json\s*|```$', '', raw_art_content.strip(), flags=re.MULTILINE).strip()
            
            try:
                art_data = json.loads(cleaned_art_content)
            except json.JSONDecodeError as e:
                print(f"Error JSON Parsing Art Response in force_art: {e}")
                art_data = {}

            # Estrazione delle 5 chiavi
            current_medium = art_data.get("medium_and_style", "")
            current_canvas = art_data.get("random_canvas_subject", "")
            current_mapping = art_data.get("metaphorical_mapping", "")
            current_image_prompt = art_data.get("image_prompt", "")
            current_explanation = art_data.get("explanation", "") 

            if current_image_prompt:
                # FIX ARGOMENTI MANCANTI: Passiamo un tempo di reveal default
                threading.Thread(target=run_image_generation, args=(current_image_prompt, 180000, current_medium, current_canvas, current_mapping, current_explanation), daemon=True).start()
                
            # Update the global tracker so normal chat doesn't immediately overwrite it
            previous_plant_state = new_state

        except Exception as e:
            print(f"Error in Forced Art Pipeline: {e}")

    # 4. Start the task on a background thread so the UI button doesn't freeze the server
    threading.Thread(target=run_forced_art, args=(info, (state_res, simulated_weather)), daemon=True).start()

    return jsonify(status="success")


@app.route('/wait')
def wait():
    """Serve wait page when another user is active."""
    return render_template("wait.html")


@app.route('/exit')
def exit():
    """Serve exit page after logout."""
    return render_template("exit.html")


@app.route('/inactivity')
def inactivity():
    """Serve inactivity page after auto-logout."""
    return render_template("inactivity.html")


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Handle user logout."""
    global active_session_id, last_activity, sad, angry, random_spray, random_watering, user, user_flag
    global smoothed_wellbeing

    print("-> User logout")
    sad = False
    angry = False
    smoothed_wellbeing = 1.0  # Reset the smoothed wellbeing on logout

    # Notify Raspberry Pi of logout
    if rsb_connected:
        socketio.emit("reset_mood")
        socketio.emit("clear_request")
        socketio.emit("log_out")

    user_flag = False
    random_watering = False
    random_spray = False
    active_session_id = None

    return jsonify({'status': 'success'})


def to_logout():
    """Automatic logout due to inactivity."""
    global active_session_id, last_activity, sad, angry, random_spray, random_watering, user, user_flag
    global smoothed_wellbeing

    print("-> User to_logout")
    sad = False
    angry = False
    smoothed_wellbeing = 1.0  # Reset the smoothed wellbeing on logout

    if rsb_connected:
        socketio.emit("reset_mood")
        socketio.emit("clear_request")
        socketio.emit("log_out")

    user_flag = False
    random_watering = False
    random_spray = False


@app.route('/reset_last_activity', methods=['POST'])
def reset_last_activity():
    """
    Reset the last_activity timestamp for the current user
    and cancel any pending auto-logout countdown.
    """
    global last_activity, cancel_logout, user

    data = request.get_json()
    user_id = data.get('user_id')

    if user and user_id == user["user_id"]:
        last_activity = datetime.now()
        cancel_logout = True

    return jsonify({'status': 'success'})


@app.route('/to_logout', methods=['POST'])
def timer_to_logout():
    """Start a logout countdown for the current user."""
    global cancel_logout, user

    try:
        data = request.get_json()
        user_id = data.get('user_id')

        if user and user_id == user["user_id"]:

            cancel_logout = False

            socketio.start_background_task(logout_countdown, 60)

    except Exception as e:
        print("Error on logout timer: ", e)

    return jsonify({'status': 'success'})


def logout_countdown(seconds):
    """
    Background countdown before automatically logging out the user.
    """
    global cancel_logout

    print(f"Logout countdown started ({seconds}s)")

    for _ in range(seconds):
        socketio.sleep(1)
        if cancel_logout:
            print("Logout canceled before completion.")
            return

    print("Logout timer expired.")
    socketio.start_background_task(to_logout)


@app.route('/send_name', methods=['POST'])
def get_username():
    """Set or update the username for the current user."""
    global user
    try:
        last_activity = datetime.now()
        username = request.json['username']

        check = name_check(username)

        if check:

            user["name"] = username

            user_col = db.collection("users").document(user["user_id"])

            user_col.update({
                "username": username
            })

            return jsonify(status="success")
        else:
            return jsonify(status="error")

    except Exception as e:
        print("Error on getting name: ", e)


def user_info(user_id, returned=False):
    global user, new_user, smoothed_wellbeing  # <-- Inclusione della variabile globale per l'inerzia emotiva

    try:
        user_col = db.collection("users").document(user_id)
        user_info = user_col.get()

        if user_info.exists:
            user_data = user_info.to_dict()
            last_login = user_data["last_login"].replace(tzinfo=None)

            if (datetime.now() - last_login) <= timedelta(hours=2) and user_data["random_requests"] is True:
                request = True
            else:
                request = False

            sessions = 1

            if datetime.now() - user_data["last_login"].replace(tzinfo=None) > timedelta(hours=5) and "sessions" in user_data:
                sessions = user_data["sessions"] + 1
            elif "sessions" in user_data:
                sessions = user_data["sessions"]

            if "form_submitted" in user_data:
                user_col.update({
                    "last_login":  datetime.now(),
                    "random_requests": request,
                    "sessions": sessions,
                })
            else:
                user_col.update({
                    "last_login":  datetime.now(),
                    "random_requests": request,
                    "form_submitted": False,
                    "sessions": sessions,
                })

            user = user_col.get().to_dict()
            user["user_id"] = user_id
            user["requests_fulfilled"] = False

            # Allineamento dell'indice filtrato iniziale (EMA) al mood storico caricato da Firestore
            # Supporta sia le vecchie stringhe in greco sia quelle nuove in inglese
            if user["mood"] in ["Χαρούμενο", "Happy"]:
                smoothed_valence, smoothed_arousal = 1.0, 0.2
            elif user["mood"] in ["Λυπημένο", "Sad"]:
                smoothed_valence, smoothed_arousal = -0.4, -0.5
            else:
                smoothed_valence, smoothed_arousal = -0.5, 0.6

            if not returned:
                initialize_llm(user["mood"])

        else:
            new_user = True

            moods = ["Happy", "Grumpy", "Sad"]
            mood = random.choice(moods)

            user_col.set({
                "messages": 0,
                "mood": mood,
                "random_requests": False,
                "last_login": datetime.now(),
                "form_submitted": False,
                "sessions": 1,
            })

            user = user_col.get().to_dict()
            user["user_id"] = user_id
            user["mood"] = mood
            user["requests_fulfilled"] = False

            # Allineamento dell'indice filtrato iniziale (EMA) per il nuovo utente appena creato
            if mood == "Happy":
                smoothed_valence, smoothed_arousal = 1.0, 0.2
            elif mood == "Sad":
                smoothed_valence, smoothed_arousal = -0.4, -0.5
            else:
                smoothed_valence, smoothed_arousal = -0.5, 0.6

            initialize_llm(user["mood"])

    except Exception as e:
        print("Error on getting/set user info: ", e)
        return


def flower_state(data):
    """
    Determine the current state of the flower based on sensor data.
    It may trigger random watering/spray requests (if random_requests enabled)
    """
    global random_watering, random_spray, user, random_spray_time, random_watering_time

    # Default statuses
    water = "- Watering: Do not need watering."
    air_humidity = "- Air humidity: Ideal.\n- Leaf spray: Do not need."
    light = "- Light: Ideal."
    temp = "- Temperature: Ideal."

    # Check soil moisture for watering need
    if data["need_watering"]:

        if data["soil_moisture"] > THRESHOLDS.soil_moisture_min + 500:
            water = "- Watering: Yes, immediately!!"
        else:
            water = "- Watering: Yes, i need."

    # Random watering request
    elif random_requests and user["random_requests"] == False and user["requests_fulfilled"] == False:
        if not random_watering and (watered_time is None or datetime.now() - watered_time > timedelta(hours=1)) and data["soil_moisture"] >= 1900 and random.random() <= 0.5:
            water = "- Watering: Need a very small amount of water."
            user["random_requests"] = True
            random_watering = True
            random_watering_time = datetime.now()
            threading.Thread(target=update_user, args=(
                user,), daemon=True).start()
            socketio.start_background_task(send_request, "water")
    elif random_watering:
        water = "- Watering: Need a very small amount of water."

    day = is_day()
    shadow_time = data["shadow_time"]
    sun_time = data["sun_time"]

    # Check light levels
    if data["lux"] < THRESHOLDS.light_min and day:
        if shadow_time is not None:
            if shadow_time:
                light = "- Light: low sun exposure, need more sunlight !"

    elif data["lux"] > THRESHOLDS.light_max and day:
        if sun_time is not None:
            if sun_time:
                light = "- Light: high sun exposure!!"

    # Check temperature levels
    if data["low_temp"]:
        if data["temp"] < 12:
            temp = "- Temperature: less than 12 degrees. Very low temperature!!"
        else:
            temp = "- Temperature: low temperature!"

    elif data["high_temp"]:
        temp = "- Temperature: high temperature!!"

    # Check air humidity for spraying need
    if data["low_humidity"]:
        air_humidity = "- Air humidity: very low. Needs spraying water on leaves!"

    # Random spray request
    elif random_requests and not random_watering and user["random_requests"] == False and user["requests_fulfilled"] == False:
        if not random_spray and (spray_status is None or datetime.now() - spray_status > timedelta(hours=1)):
            air_humidity = "- Air humidity: low. Needs spraying water on leaves!"
            user["random_requests"] = True
            random_spray = True
            random_spray_time = datetime.now()
            threading.Thread(target=update_user, args=(
                user,), daemon=True).start()
            socketio.start_background_task(send_request, "spray")
    elif random_spray:
        air_humidity = "- Air humidity: low. Needs spraying water on leaves!"

    return water, light, temp, air_humidity


def send_request(request):
    """Send a random watering/spray request to Firestore and notify Raspberry Pi."""
    global request_key

    try:
        req = {
            "user": user["user_id"],
            "request": request,
            "time": random_spray_time if request == "spray" else random_watering_time,
            "fulfilled": None
        }

        ref = db.collection("requests").document()
        ref.set(req)
        request_key = ref.id
        socketio.emit("request", {"request": request})
    except Exception as e:
        print("Error sending request: ", e)


def send_flower_need(need, data=None):
    """
    Create a new 'flower_needs' document for a specific need
    and send its key back to the Raspberry.
    """

    if user and datetime.now() - last_activity < timedelta(minutes=3):
        id = user["user_id"]
    else:
        id = None

    if data is None:
        flower_need = {
            "need": need,
            "time": datetime.now(),
            "user connected on need": id,
            "fulfilled": None,
            "user connected on fulfillment": None
        }
    else:
        flower_need = {
            "need": need,
            "detail": data,
            "time": datetime.now(),
            "user connected on need": id,
            "fulfilled": None,
            "user connected on fulfillment": None
        }

    ref = db.collection("flower_needs").document()
    ref.set(flower_need)

    socketio.emit("need_key", {"need_key": ref.id, "need": need})

    return ref.id


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
                key = send_flower_need(need)
                KEYS.water = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.water, "need": need})
        elif need == "low_humidity":
            if KEYS.spray is None:
                air_moisture = data.get("air_moisture")
                key = send_flower_need(need, air_moisture)
                KEYS.spray = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.spray, "need": need})
        elif need == "cold":
            if KEYS.cold is None:
                temp = data.get("temp")
                key = send_flower_need(need, temp)
                KEYS.cold = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.cold, "need": need})
        elif need == "hot":
            if KEYS.hot is None:
                temp = data.get("temp")
                key = send_flower_need(need, temp)
                KEYS.hot = key
            else:
                socketio.emit("need_key", {"need_key": KEYS.hot, "need": need})
        elif need == "low_light":
            if KEYS.low_light is None:
                light = data.get("light")
                key = send_flower_need(need, light)
                KEYS.low_light = key
            else:
                socketio.emit(
                    "need_key", {"need_key": KEYS.low_light, "need": need})
        elif need == "high_light":
            if KEYS.high_light is None:
                light = data.get("light")
                key = send_flower_need(need, light)
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

        if user and datetime.now() - last_activity < timedelta(minutes=3):
            id = user["user_id"]
            user["requests_fulfilled"] = True
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
    global random_spray, random_watering, request_key, random_watering_time, random_spray_time

    if request == "water" and random_watering and datetime.now() - random_watering_time < timedelta(minutes=3):
        # Random watering request was fulfilled
        random_watering = False
        random_watering_time = None
        req = db.collection("requests").document(request_key)
        request_key = None
        req.update({
            "fulfilled": datetime.now()
        })
    elif request == "spray":
        # Random spray request was fulfilled
        req = db.collection("requests").document(request_key)
        request_key = None
        random_spray_time = None
        random_spray = False

        req.update({
            "fulfilled": datetime.now()
        })


@socketio.on("sensors_data")
def get_flower_status(data):
    """Socket.IO handler: receive latest sensor data from Raspberry."""
    global sensors_data
    sensors_data = data


def is_day():

    city = LocationInfo("Athens", "Greece",
                        "Europe/Athens", 37.983810, 23.727539)

    now = datetime.now(pytz.timezone("Europe/Athens"))

    sun_now = sun(city.observer, date=datetime.now().date(),
                  tzinfo=city.timezone)

    if now < sun_now['sunrise']:
        return False
    elif now < sun_now['sunset']:
        return True
    else:
        return False


def initialize_llm(choice):
    """Initialize LLM chat history with selected personality mood."""
    global chat_history, angry, sad

    # Set the boolean flags and emit to the hardware based on the choice
    if choice in ["Χαρούμενο", "Happy"]:
        angry = False
        sad = False
        socketio.emit("reset_mood")
    elif choice in ["Γκρινιάρικο", "Grumpy"]:
        angry = True
        sad = False
        socketio.emit("angry_mode")
    elif choice in ["Λυπημένο", "Sad"]:
        angry = False
        sad = True
        socketio.emit("sad_mode")

    # Load only your active system prompt
    with open("system_prompt/llm_prompt_v2.txt", 'r', encoding='utf-8') as file:
        llm_prompt = file.read()

    chat_history = [{"role": "system", "content": llm_prompt}]


@socketio.on("get_thresholds")
def get_thresholds():
    """Socket.IO handler: Send current thresholds to Raspberry Pi."""

    with open("plant_thresholds.json", 'r', encoding='utf-8') as file:
        file_thresholds = json.load(file)

    if change_threshold:
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


def initialize_thresholds():
    """Load thresholds from 'config/plant_thresholds.json' into the global THRESHOLDS object."""
    # Build the path dynamically: go up two levels from src/server to the root, then into config/
    config_path = os.path.join(BASE_DIR, 'config', 'plant_thresholds.json')
    with open(config_path, 'r', encoding='utf-8') as file:
        thresholds = json.load(file)

    if change_threshold:
        thresholds["light_min"] = 100

    THRESHOLDS.soil_moisture_min = thresholds["soil_moisture_min"]
    THRESHOLDS.air_moisture_min = thresholds["air_moisture_min"]
    THRESHOLDS.temp_min = thresholds["temp_min"]
    THRESHOLDS.temp_max = thresholds["temp_max"]
    THRESHOLDS.light_min = thresholds["light_min"]
    THRESHOLDS.light_max = thresholds["light_max"]


def check_thresholds():
    """Background task: Monitor 'plant_thresholds.json' for changes and notify Raspberry Pi."""
    global thresholds_to_check, change_threshold

    config_path = os.path.join(BASE_DIR, 'config', 'plant_thresholds.json')
    while True:
        with open(config_path, 'r', encoding='utf-8') as file:
            thresholds = json.load(file)

        # Initialize thresholds
        if thresholds_to_check is None:
            thresholds_to_check = thresholds.copy()

        else:

            if change_threshold:
                thresholds["light_min"] = 100

            # If file contents changed, update and notify Raspberry
            if thresholds != thresholds_to_check:
                thresholds_to_check = thresholds.copy()

                initialize_thresholds()

                if rsb_connected:
                    socketio.emit("thresholds_updated")

        socketio.sleep(60)


def initialize_keys():
    """Initialize KEYS dataclass from 'need_keys.json'."""
    global KEYS
    
    config_path = os.path.join(BASE_DIR, 'config', 'need_keys.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            data_from_file = json.load(file)
            KEYS = NeedKeys(**data_from_file)
    except FileNotFoundError:
        print(f"Error: Το αρχείο 'need_keys.json' δεν βρέθηκε.")


def auto_clear():
    global user_flag

    while True:
        if user_flag and user and rsb_connected and last_activity is not None and datetime.now()-last_activity > timedelta(minutes=3):
            print("-> Auto clear after inactivity")
            socketio.emit("reset_mood")
            socketio.emit("log_out")
            user_flag = False

        socketio.sleep(10)

def run_image_generation(prompt, reveal_duration, medium="", canvas="", mapping="", explanation=""):
    """Sends the prompt generated by AI 1 to the external cluster endpoint."""
    url = "http://150.140.142.76:9999/generate"
    payload = {
        "prompt": prompt,
        "height": 1024,
        "width": 768,
        "num_inference_steps": 20,
        "guidance_scale": 10.0
    }
    try:
        res = requests.post(url, json=payload, timeout=120)
        if res.status_code == 200:
            art_path = os.path.join(BASE_DIR, "assets", "art.png")
            with open(art_path, "wb") as f:
                f.write(res.content)
            print("New artwork saved successfully as art.png")
            # --- MODIFICA QUI: Invia tutti i dati al client ---
            socketio.emit("new_art_available", {
                "duration": reveal_duration,
                "medium": medium,
                "canvas": canvas,
                "mapping": mapping,
                "explanation": explanation
            })
        else:
            print(f"Image API returned status code: {res.status_code}")
    except Exception as e:
        print(f"Error calling Image Generation API: {e}")

@app.route('/get_art', methods=['GET'])
def get_art():
    art_path = os.path.join(BASE_DIR, "assets", "art.png")
    """Endpoint exposing the generated art file for download by the Pi."""
    if os.path.exists(art_path):
        return send_file(art_path, mimetype="image/png")
    return jsonify(error="No art piece compiled yet"), 404

def calculate_reveal_duration(sensor_data):
    """
    Calcola la durata del Progressive Capillary Reveal (in ms) 
    in funzione dello stress termico e idrico della pianta.
    """
    base_duration = 180000  # 3 minuti (valore nominale)
    
    # Coefficiente di stress termico (Temperatura ottimale > 18°C)
    temp = sensor_data.get("temp", 22.0)
    low_temp = sensor_data.get("low_temp", False)
    high_temp = sensor_data.get("high_temp", False)
    
    if low_temp or temp < 18.0:
        # Rallentamento cinetico proporzionale al freddo (fino a +150%)
        alpha_T = 1.0 + min(1.5, (18.0 - temp) * 0.15)
    elif high_temp:
        alpha_T = 1.2
    else:
        alpha_T = 1.0
        
    # Coefficiente di stress idrico (soil_moisture)
    need_watering = sensor_data.get("need_watering", False)
    if need_watering:
        # Incremento del tempo in base alla severità dell'inaridimento
        alpha_M = 1.5
        if sensor_data.get("soil_moisture", 0) > 2300:
            alpha_M = 2.1
    else:
        alpha_M = 1.0
        
    # Calcolo del tempo totale combinato
    total_duration = int(base_duration * alpha_T * alpha_M)
    
    # Guardrail di sicurezza:bound tra 1.5 e 8 minuti
    return max(90000, min(480000, total_duration))

def analyze_user_sentiment(text):
    """
    Extracts text sentiment using the local LLM endpoint (LM-Studio)
    instead of PyTorch, mapping the score between -1.0 and 1.0.
    """
    try:
        prompt = (
            "Analyze the sentiment of the following user message. "
            "Respond STRICTLY with a single float number between -1.0 (extremely negative/angry) "
            "and 1.0 (extremely positive/happy). Neutral messages must be 0.0. "
            "Do not include any text, reasoning, or markdown blocks. Only the raw number.\n\n"
            f"User message: '{text}'"
        )
        
        response = client.chat.completions.create(
            model=llm_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # Zero per garantire il massimo determinismo ed evitare variazioni creative
            max_tokens=10
        )
        
        output = response.choices[0].message.content.strip()
        # Regex per estrarre in sicurezza il valore numerico dall'output testuale
        match = re.findall(r"[-+]?\d*\.\d+|\d+", output)
        if match:
            sentiment_score = float(match[0])
            return max(-1.0, min(1.0, sentiment_score))
        return 0.0
    except Exception as e:
        print(f"Error in LLM Sentiment Analysis: {e}")
        return 0.0
    
def update_plant_mood(sensor_data, user_sentiment, delta_time):
    """
    Combines physical telemetry and user sentiment to determine the continuous 
    2D affect vector, applying an asymmetric EMA filter (Negativity Bias).
    """
    global angry, sad, smoothed_valence, smoothed_arousal
    
    # 1. Calculate instant Valence
    hardware_score = 1.0
    if sensor_data.get("need_watering", False): 
        hardware_score -= 0.5
    if sensor_data.get("low_temp", False) or sensor_data.get("high_temp", False): 
        hardware_score -= 0.3
    if sensor_data.get("low_humidity", False): 
        hardware_score -= 0.2
        
    V_instant = (0.6 * hardware_score) + (0.4 * user_sentiment)
    
    # 2. Calculate instant Arousal
    f_interaction = math.exp(-delta_time / 60.0) if delta_time is not None else 0.5
    stress_intensity = (0.4 if sensor_data.get("need_watering", False) else 0.0) + \
                       (0.3 if (sensor_data.get("low_temp", False) or sensor_data.get("high_temp", False)) else 0.0)
        
    A_instant = (0.5 * f_interaction) + (0.5 * stress_intensity)
    A_instant = (A_instant * 2.0) - 1.0  
    
    # 3. Dynamic Asymmetric Smoothing (Negativity Bias)
    # If things are getting worse (instant input < current memory), respond faster.
    if V_instant < smoothed_valence:
        alpha_v = 0.45  # Fast decay under stress/criticism
    else:
        alpha_v = 0.20  # Slow, cautious recovery when conditions improve
        
    alpha_a = 0.30  # Standard smoothing factor for the energy axis
    
    # Apply low-pass recursive filters
    smoothed_valence = (alpha_v * V_instant) + ((1.0 - alpha_v) * smoothed_valence)
    smoothed_arousal = (alpha_a * A_instant) + ((1.0 - alpha_a) * smoothed_arousal)
    
    # 4. 2D Quadrant Classification
    if smoothed_valence > 0.1:
        current_mood = "Excited" if smoothed_arousal > 0.0 else "Calm"
        angry, sad = False, False
    else:
        if smoothed_arousal > 0.0:
            current_mood = "Anxious"
            angry, sad = True, False
        else:
            current_mood = "Lethargic"
            angry, sad = False, True
            
    return current_mood, V_instant, A_instant


if __name__ == '__main__':
    global personality_check, random_requests

    random_requests = False

    parser = argparse.ArgumentParser()
    parser.add_argument("--request", choices=["yes", "no"], default="no")

    args = parser.parse_args()

    random_requests = True if args.request == "yes" else False

    personality_check = "Server"
    initialize_thresholds()

    initialize_keys()

    socketio.start_background_task(target=check_thresholds)

    threading.Thread(target=get_weather, daemon=True).start()

    while current_weather is None:
        time.sleep(1)

    socketio.start_background_task(auto_clear)

    socketio.run(
        app,
        host="0.0.0.0",
        port=5000,
        debug=False
    )
