import socketio
from dataclasses import dataclass
from plantScreen import Display
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer, Qt
from datetime import datetime, timedelta, timezone
from expressions_path import *
from tools import *
import hmac
import hashlib
import busio
import adafruit_veml7700
import board
import adafruit_dht
import os
from dotenv import load_dotenv
import sys
import threading
import json
import time
from DFRobot_RaspberryPi_Expansion_Board import DFRobot_Expansion_Board_IIC as Board

sio = socketio.Client()

# API key used for HMAC authentication with the server.
# It is read from the environment for security reasons.
#API_KEY = os.environ.get("your_api_raspberry_key").encode()
load_dotenv()
api_secret = os.getenv("API_KEY")
API_KEY = api_secret.encode()

TEXT_THRESHOLD = timedelta(minutes=2) # Time after which LLM text is auto-cleared

shadow_time = None # When plant entered low-light state
sun_time = None # When plant entered high-light state
normal_light_time = None # Time spent in normal light range

text_time = None # When the last LLM text was shown
llm_response = False # True while an LLM response sequence is active
new_response = False # True if a new response comes while one is active

watered = False # True right after plant is watered
loading = True
expressions = []  # List of expressions to be displayed
new_expressions = False # True if new expressions are set
water_expressions = thanks() # Expressions shown after successful watering
response_expressions = [] # Expressions (GIFs/emojis) from LLM response

spray_status = False # True if plant was recently sprayed
spray_time = None # Timestamp of last spray
current_weather = None # Weather description from server

angry_mood = False # Angry mood
sad_mood = False # Sad mood

request = None # Random request from server ("water" / "spray")
last_avg_soil = None # Used to detect actual watering change
last_interaction = datetime.now()  # Last time the user interacted

need_watering = False # Soil moisture low => needs watering
low_humidity_check = False  # Low air humidity => needs spray
low_temp = False # Low temperature 
high_temp = False # High temperature


@dataclass
class SensorsReadings:
    """Store current sensor readings."""
    soil_moisture: list[float]
    air_moisture: float
    temp: float
    lux: float


@dataclass
class Thresholds:
    """Store threshold values for plant needs."""
    soil_moisture_min: float = 2100
    air_moisture_min: float = 40
    temp_min: float = 18.0
    temp_max: float = 28.0
    light_min: float = 300
    light_max: float = 15000
    shadow = timedelta(minutes=2)
    sun = timedelta(minutes=2)
    light_normal = timedelta(minutes=1)
    spray = timedelta(hours=3)


THRESHOLDS = Thresholds()


@dataclass
class NeedKeys:
    """Store server-side keys for each active plant need."""
    water: str = None # Key for "need watering"
    spray: str = None # Key for "low humidity"
    hot: str = None # Key for "high temperature"
    cold: str = None # Key for "low temperature"
    low_light: str = None # Key for "low light exposure"
    high_light: str = None # Key for "high sun exposure"

KEYS = NeedKeys()

@sio.on("thresholds_updated")
def thresholds_updated():
    """Handle thresholds update event from server to refresh local thresholds."""
    get_thresholds()


@sio.on("angry_mode")
def angry_flower():
    """Socket.IO handler: switch flower to angry mood."""
    global angry_mood, sad_mood

    sad_mood = False
    angry_mood = True


@sio.on("sad_mode")
def sad_flower():
    """Socket.IO handler: switch flower to sad mood."""
    global sad_mood, angry_mood

    angry_mood = False
    sad_mood = True


@sio.on("reset_mood")
def reset_mood():
    """Reset mood flags to default (no angry/sad)."""
    global sad_mood, angry_mood

    sad_mood = False
    angry_mood = False


@sio.on("mood")
def get_mood():
    """
    Socket.IO handler: send current flower sensors status back to server.
    
    - Marks that an LLM response is expected.
    - Prepares a compact sensor data payload.
    - Emits 'sensors_data' with the latest readings and flags.
    """
    global window, llm_response, index_response, new_response, last_interaction

    # Reset response index and update last interaction time
    index_response = 0
    last_interaction = datetime.now()

    # If an LLM response is already in progress, mark that a new one is coming
    if llm_response:
        new_response = True

    llm_response = True

    # Clear any previous LLM text from the screen
    window.clear_text_signal.emit("", False)

    # Prepare sensor data payload
    try:
        if shadow_time is None:
            sh_time = None
        elif (datetime.now() - shadow_time) > THRESHOLDS.shadow:
            sh_time = True
        else:
            sh_time = False
    except Exception as e:
        print(f"Error calculating shadow time: {e}")
        sh_time = None

    try:
        if sun_time is None:
            sn_time = None
        elif (datetime.now() - sun_time) > THRESHOLDS.sun:
            sn_time = True
        else:
            sn_time = False
    except Exception as e:
        print(f"Error calculating sun time: {e}")
        sn_time = None

    try:
        if spray_status and sensors.air_moisture < THRESHOLDS.air_moisture_min:
            air_moisture = 41
        else:
            air_moisture = sensors.air_moisture
    except Exception as e:
        print(f"Error getting air moisture: {e}")
        air_moisture = None

    # Build data payload
    try:
        avg_soil = sum(sensors.soil_moisture) / len(sensors.soil_moisture)

        data = {
            "low_humidity": low_humidity_check,
            "need_watering": need_watering,
            "low_temp": low_temp,
            "high_temp": high_temp,
            "soil_moisture": avg_soil,
            "air_moisture": air_moisture,
            "temp": sensors.temp,
            "lux": sensors.lux,
            "shadow_time": sh_time,
            "sun_time": sn_time,
            "spray_status": spray_status,
            "mood": flower_mood,
        }
    except Exception as e:
        print(f"Error preparing data: {e}")
        data = {}

    # Emit data to the server
    try:
        sio.emit("sensors_data", {"sensor": data})
    except Exception as e:
        print(f"Error emitting sensors data: {e}")


@sio.on("response")
def display_message(data):
    """
    Display the anwser of the LLM on the plant screen.
    
    - Shows the answer text on the screen.
    - Clears / updates the global response_expressions list based on emojis.
    """
    global text_time, window, response_expressions, last_interaction

    last_interaction = datetime.now()
    text_time = datetime.now()

    if "error" not in data:

        try:
            # Show answer text
            window.llm_text_signal.emit(data["answer"], False)

            # Refresh response expressions list
            response_expressions.clear()

            if len(data["emojis"]) > 0:
                data["emojis"] = check_gifs(data["emojis"])
                if len(data["emojis"]) > 0:
                    response_expressions.extend(data["emojis"])

        except Exception as e:
            print(f"Error processing response: {e}")

    window.display_finished_signal.emit()


def check_gifs(gifs_path):
    """Ensure that gif paths exist; return only valid paths."""

    fixed_paths = []

    try:
        for path in gifs_path:

            # Non-gif entries are passed through
            if not path.endswith(".gif"):
                fixed_paths.append(path)
                continue

            # Ensure path starts with 'expressions/'
            if not path.startswith("expressions/"):
                path = f"expressions/{path}"

            # Only keep if file actually exists
            if os.path.exists(path):
                fixed_paths.append(path)
    except Exception as e:
        print(f"Error checking gifs: {e}")

    return fixed_paths


@sio.on("need_key")
def get_key(data):
    """Receive and store the server key for a specific plant need."""
    global KEYS

    key = data.get("need_key")
    need = data.get("need")

    if need == "water" and KEYS.water == "waiting":
        KEYS.water = key
    elif need == "low_humidity" and KEYS.spray == "waiting":
        KEYS.spray = key
    elif need == "cold" and KEYS.cold == "waiting":
        KEYS.cold = key
    elif need == "hot" and KEYS.hot == "waiting":
        KEYS.hot = key
    elif need == "low_light" and KEYS.low_light == "waiting":
        KEYS.low_light = key
    elif need == "high_light" and KEYS.high_light == "waiting":
        KEYS.high_light = key


@sio.on("request")
def get_request(data):
    """Handle random water/spray request sent by the server."""
    global request, last_avg_soil

    request = data.get("request")

    if request == "water":
        last_avg_soil = sum(sensors.soil_moisture) / len(sensors.soil_moisture)


@sio.on("spray")
def spray():
    """Handle spray request: update spray status and time. Send to server."""
    global spray_status, spray_time

    try:
        spray_status = True
        spray_time = datetime.now()

        data = {
            "last_spray": spray_time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # Notify server
        sio.emit("spray_status", {
                 "spray_status": spray_time.strftime("%Y-%m-%d %H:%M:%S")})

         # Save last spray time
        with open("spray_status.json", 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=2)
    except Exception as e:
        print(f"Error handling spray request: {e}")
        spray_status = False
        spray_time = None


@sio.on("weather")
def set_weather(weather):
    """Update current weather."""
    global current_weather

    if weather is not None:
        current_weather = weather
        

@sio.on("log_out")
def clear_text():
    """Clear LLM response text from display."""
    global window

    window.llm_text_signal.emit("", False)


@sio.on("thresholds")
def set_thresholds(data):
    """Update threshold values received from the server."""
    global THRESHOLDS, TEXT_THRESHOLD

    THRESHOLDS.soil_moisture_min = data.get("soil_moisture_min")
    THRESHOLDS.air_moisture_min = data.get("air_moisture_min")
    THRESHOLDS.temp_min = data.get("temp_min")
    THRESHOLDS.temp_max = data.get("temp_max")
    THRESHOLDS.light_min = data.get("light_min")
    THRESHOLDS.light_max = data.get("light_max")
    THRESHOLDS.shadow = timedelta(minutes=data.get("shadow"))
    THRESHOLDS.sun = timedelta(minutes=data.get("sun"))
    THRESHOLDS.light_normal = timedelta(seconds=data.get("light_normal"))
    THRESHOLDS.spray = timedelta(hours=data.get("spray"))
    TEXT_THRESHOLD = timedelta(minutes=data.get("text_threshold"))


def info_format(sensors_data):
    """Format sensor readings into a multi-line string for debug."""
    text = f"Υγρασία Αέρα: {sensors_data.air_moisture:4.1f}% ({THRESHOLDS.air_moisture_min}%-100%)\n" + \
        f"Υγρασία Χώματος: {sensors_data.soil_moisture[-1]} (1000-{THRESHOLDS.soil_moisture_min})\n" + \
        f"Θερμοκρασία: {sensors_data.temp:4.2f}°C ({THRESHOLDS.temp_min}°C-{THRESHOLDS.temp_max}°C)\n" + \
        f"Φως: {sensors_data.lux:.2f} lx ({THRESHOLDS.light_min} lx-{THRESHOLDS.light_max} lx)\n"

    return text


@sio.on("clear_request")
def clear_request():
    """Clear current random request (water/spray)."""
    global request

    request = None

def evaluate_state(sensor_data):
    """Evaluate current sensor readings and decide mood, expressions, needs, and log information.

     This function:
    - Checks soil moisture and decides if watering is needed or fulfilled.
    - Checks temperature against min/max thresholds (cold / hot).
    - Checks light exposure over time.
    - Checks air humidity and decides if leaf spray is needed.
    - Chooses appropriate expressions and mood label.
    - Builds a log entry for the current sensor state.
    """
    global shadow_time, sun_time, normal_light_time, spray_status, \
        flower_mood, current_weather, last_avg_soil, request, KEYS, need_watering, low_humidity_check, low_temp, high_temp

    flower_mood = None
    mood_score = 0
    expressions = []
    needs = []
    need_water = False

    try:
        # --- Soil moisture / watering state ---
        avg_soil = sum(sensors.soil_moisture) / len(sensors.soil_moisture)

        # If soil is too dry, start need for watering 
        if (need_watering and avg_soil > THRESHOLDS.soil_moisture_min - 150) or avg_soil > THRESHOLDS.soil_moisture_min:
            mood_score += 1
            need_water = True
            needs.append("water")
            expressions.extend(water())
            need_watering = True

            # Notify server about new water need
            if KEYS.water is None and sio.connected:
                    KEYS.water = "waiting"
                    sio.emit("send_need", {"need": "water"})

        # If had a water *request* from the server, check if it was satisfied
        elif request == "water":
            if last_avg_soil is not None and last_avg_soil - avg_soil > 80 and sio.connected:
                    sio.emit("request_completed", {"request": "water"})
                    request = None

        # If soil is back to normal, clear water need state
        elif avg_soil < THRESHOLDS.soil_moisture_min - 50 and need_watering:
            need_water = False
            need_watering = False
            if KEYS.water is not None and KEYS.water != "waiting" and sio.connected:
                sio.emit("need_fulfilled", {"key": KEYS.water})
                KEYS.water = None

         # --- Temperature (cold) ---
        if (low_temp and sensor_data.temp < THRESHOLDS.temp_min + 1) or sensor_data.temp < THRESHOLDS.temp_min:
            mood_score += 1
            expressions.extend(cold())
            needs.append("low_temp")
            low_temp = True

            # Notify server about new cold need
            if KEYS.cold is None and sio.connected:
                    KEYS.cold = "waiting"
                    sio.emit("send_need", {
                             "need": "cold", "temp": sensor_data.temp})

            # Extreme low temperature warning
            if sensor_data.temp < 12:
                expressions.append(warning())
                expressions.append(warning())
                expressions.append(warning())
                needs.append("extreme_low_temp")

        # Temperature back to normal from cold
        elif KEYS.cold is not None and KEYS.cold != "waiting" and sio.connected:
            sio.emit("need_fulfilled", {"key": KEYS.cold})
            KEYS.cold = None
            low_temp = False

        # --- Temperature (hot) ---
        if (high_temp and sensor_data.temp > THRESHOLDS.temp_max - 1) or sensor_data.temp > THRESHOLDS.temp_max:
            mood_score += 1
            expressions.extend(hot())
            needs.append("high_temp")
            high_temp= True

             # Notify server about new hot need
            if KEYS.hot is None and sio.connected:
                    KEYS.hot = "waiting"
                    sio.emit("send_need", {"need": "hot",
                             "temp": sensor_data.temp})

        # Temperature back to normal from hot
        elif KEYS.hot is not None and KEYS.hot != "waiting" and sio.connected:
            sio.emit("need_fulfilled", {"key": KEYS.hot})
            KEYS.hot = None
            high_temp= False


        # --- Light / sun exposure ---
        day = is_day()

        # Too low light
        if sensor_data.lux < THRESHOLDS.light_min and day:
            if shadow_time is None:

                # Start tracking time of low light
                shadow_time = datetime.now()

                # If it were in sun too long before, start high sun exposure need
                if sun_time is not None and (datetime.now() - sun_time) > THRESHOLDS.sun:
                    mood_score += 1
                    expressions.extend(high_sun_exposure())
                    needs.append("high_sun_exposure")
                    if KEYS.high_light is None and sio.connected:
                            KEYS.high_light = "waiting"
                            sio.emit("send_need", {
                                     "need": "high_light", "light": sensor_data.lux})

            # Long low light exposure start new low sun exposure need
            elif (datetime.now() - shadow_time) > THRESHOLDS.shadow:
                mood_score += 1
                expressions.extend(low_sun_exposure())
                needs.append("low_sun_exposure")
                if KEYS.low_light is None and sio.connected:
                        KEYS.low_light = "waiting"
                        sio.emit("send_need", {
                                 "need": "low_light", "light": sensor_data.lux})

                if sun_time is not None:
                    sun_time = None

            # Long time in high light exposure start new high sun exposure need
            elif sun_time is not None and (datetime.now() - sun_time) > THRESHOLDS.sun:
                mood_score += 1
                expressions.extend(high_sun_exposure())
                needs.append("high_sun_exposure")
                if KEYS.high_light is None and sio.connected:
                        KEYS.high_light = "waiting"
                        sio.emit("send_need", {
                                 "need": "high_light", "light": sensor_data.lux})

        # High light exposure
        if sensor_data.lux > THRESHOLDS.light_max and day:
            if sun_time is None:

                # Start tracking time in high light
                sun_time = datetime.now()

                # If it were in shadow too long before, start low sun exposure need
                if shadow_time is not None and (datetime.now() - shadow_time) > THRESHOLDS.shadow:
                    mood_score += 1
                    expressions.extend(low_sun_exposure())
                    needs.append("low_sun_exposure")
                    if KEYS.low_light is None and sio.connected:
                            KEYS.low_light = "waiting"
                            sio.emit("send_need", {
                                     "need": "low_light", "light": sensor_data.lux})

            # Long high light exposure
            elif (datetime.now() - sun_time) > THRESHOLDS.sun:
                mood_score += 1
                expressions.extend(high_sun_exposure())
                needs.append("high_sun_exposure")
                if KEYS.high_light is None and sio.connected:
                        KEYS.high_light = "waiting"
                        sio.emit("send_need", {
                                 "need": "high_light", "light": sensor_data.lux})

                if shadow_time is not None:
                    shadow_time = None
            
            # If it were in shadow too long before, start low sun exposure need
            elif shadow_time is not None and (datetime.now() - shadow_time) > THRESHOLDS.shadow:
                mood_score += 1
                expressions.extend(low_sun_exposure())
                needs.append("low_sun_exposure")
                if KEYS.low_light is None and sio.connected:
                        sio.emit("send_need", {
                                 "need": "low_light", "light": sensor_data.lux})
                        KEYS.low_light = "waiting"

        # Light back to normal range
        if sensor_data.lux > THRESHOLDS.light_min and sensor_data.lux < THRESHOLDS.light_max:
            if normal_light_time is not None:
                # If we stayed in normal light long enough, clear previous light needs
                if (datetime.now() - normal_light_time) > THRESHOLDS.light_normal:
                    if shadow_time is not None:
                        shadow_time = None
                        if KEYS.low_light is not None and KEYS.low_light != "waiting" and sio.connected:
                                sio.emit("need_fulfilled", {
                                         "key": KEYS.low_light})
                                KEYS.low_light = None
                    elif sun_time is not None:
                        sun_time = None
                        if KEYS.high_light is not None and KEYS.high_light != "waiting" and sio.connected:
                            sio.emit("need_fulfilled", {
                                "key": KEYS.high_light})
                            KEYS.high_light = None
                    normal_light_time = None
            elif normal_light_time is None and (shadow_time is not None or sun_time is not None):
                # Just entered normal light after shadow/sun exposure 
                normal_light_time = datetime.now()

            # Long time in shadow start new low sun exposure need
            if shadow_time is not None and (datetime.now() - shadow_time) > THRESHOLDS.shadow:
                mood_score += 1
                expressions.extend(low_sun_exposure())
                needs.append("low_sun_exposure")
                if KEYS.low_light is None:
                    if sio.connected:
                        KEYS.low_light = "waiting"
                        sio.emit("send_need", {
                                 "need": "low_light", "light": sensor_data.lux})
                        
            # Long time in high light exposure start new high sun exposure need
            elif sun_time is not None and (datetime.now() - sun_time) > THRESHOLDS.sun:
                mood_score += 1
                expressions.extend(high_sun_exposure())
                needs.append("high_sun_exposure")
                if KEYS.high_light is None:
                    if sio.connected:
                        KEYS.high_light = "waiting"
                        sio.emit("send_need", {
                                 "need": "high_light", "light": sensor_data.lux})

        # --- Air humidity / spray logic ---
        # If enough time passed since last spray and we don't have an active spray request,
        # allow new low-humidity alerts again.
        if spray_time is not None and (datetime.now() - spray_time) > THRESHOLDS.spray and request != "spray":
            spray_status = False

        # Low humidity and not recently sprayed start new need spray
        if (low_humidity_check and sensor_data.air_moisture < THRESHOLDS.air_moisture_min + 1) or sensor_data.air_moisture < THRESHOLDS.air_moisture_min and not spray_status:
            mood_score += 1
            expressions.append(low_humidity())
            needs.append("low_humidity")
            low_humidity_check = True

            if KEYS.spray is None and sio.connected:
                    KEYS.spray = "waiting"
                    sio.emit("send_need", {
                             "need": "low_humidity", "air_moisture": sensor_data.air_moisture})
        # Air humidity back to normal from low, need fulfilled
        elif KEYS.spray is not None and KEYS.spray != "waiting" and sio.connected and request != "spray":
            low_humidity_check = False

            if spray_time is not None and datetime.now()-spray_time < timedelta(minutes=2):
                sio.emit("need_fulfilled", {"key": KEYS.spray, "spray": True})
            else:
                sio.emit("need_fulfilled", {"key": KEYS.spray, "spray": False})
            KEYS.spray = None

        # Random spray request handling
        elif request == "spray":

            if spray_time is not None and (datetime.now() - spray_time) < THRESHOLDS.spray:
                if sio.connected:
                    sio.emit("request_completed", {"request": "spray"})
                    request = None

         # --- Decide overall mood based on mood_score ---
        if mood_score == 0:

            if angry_mood:
                path = angry()
            elif sad_mood:
                path = sad()
            else:
                path = good(current_weather)

            expressions.extend(path)
            needs.append("good")
            flower_mood = "good"
        elif mood_score == 1:
            expressions.append(neutral())
            needs.append("neutral")
            flower_mood = "neutral"
        elif mood_score == 2 or mood_score == 3:
            path = sad()
            expressions.extend(path)
            needs.append("sad")
            flower_mood = "sad"
        else:
            expressions.append(cry())
            expressions.append(cry_static())
            needs.append("cry")
            flower_mood = "cry"

        # Build log entry for this evaluation
        log = {
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "low_humidity": low_humidity_check,
            "need_watering": need_watering,
            "low_temp": low_temp,
            "high_temp": high_temp,
            "temperature": sensor_data.temp,
            "air_humidity": sensor_data.air_moisture,
            "soil_humidity": sensor_data.soil_moisture[-1],
            "avg_soil_humidity": avg_soil,
            "light": sensor_data.lux,
            "watered": None,
            "water_spray": None,
            "mood": flower_mood
        }
    except Exception as e:
        print(f"Error evaluating state: {e}")
        expressions.extend(good(current_weather))
        needs = ["good"]
        need_water = False
        log = {
            "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "low_humidity": None,
            "need_watering": need_watering,
            "low_temp": low_temp,
            "high_temp": high_temp,
            "temperature": None,
            "air_humidity": None,
            "soil_humidity": None,
            "avg_soil_humidity": None,
            "light": None,
            "watered": None,
            "water_spray": None,
            "mood": "good"
        }

    return expressions, need_water, needs, log


def show_random_text():
    """Periodically show random prompts on the screen to invite the user to interact."""
    global last_interaction

    while True:

        if datetime.now() - last_interaction > timedelta(minutes=3):
        
            if not need_watering and not low_humidity_check and not low_temp and not high_temp:

                messages = [
                    "Αν με αγνοήσεις, θα... ξεραθώ! 😢 (πλάκα κάνω… μάλλον 😅)",
                    "Μπορείς να μου μιλήσεις. Ναι, εμένα — το φυτό στη γλάστρα δίπλα σου! 🌿",
                    "Γεια! Είμαι το φυτό δίπλα σου 👋",
                    "Εδώ! Ναι, εγώ σου μιλάω — από τη γλάστρα 😄",
                    "Εμένα με λένε Ντελισιόσα. Εσένα; 🌿",
                    "Μου λείπει λίγη… παρέα! 👀🌿",
                    "Εσύ που κάθεσαι εδώ μπροστά μου… θες να μιλήσεις μαζί μου; 🙂"
                ]
            else:
                messages = [
                    "Νιώθω κάπως... περίεργα τελευταία... 😟",
                    "Είμαι λίγο 'off' σήμερα. Μπορείς να με βοηθήσεις; 🌱",
                    "Δεν ξέρω... κάτι δεν πάει καλά... 😞",
                    "Νιώθω λίγο πεσμένη... Μπορείς να με φροντίσεις; 🥀",
                    "Μου λείπει λίγη… παρέα! 👀🌿"
                ]

            selected = random.choice(messages)

            window.llm_text_signal.emit(selected, True)

            time.sleep(60)
            last_interaction = datetime.now()
            window.llm_text_signal.emit("", False)

        time.sleep(20)


def expression_refresh(sensors_data):
    """Wrapper around evaluate_state to obtain expressions, water need flag, needs list and log entry."""

    expressions = []

    expressions, need_water, needs, log = evaluate_state(sensors_data)

    return expressions, need_water, needs, log


def is_watered(prev_need_water, need_water):
    """Determine whether watering just happened between two evaluations."""
    if prev_need_water and need_water:
        return False
    elif prev_need_water and not need_water:
        return True


def save_to_json(log):
    """Append a log entry as JSON line into sensors_log.jsonl."""
    try:

        with open("sensors_log.jsonl", 'a', encoding='utf-8') as file:
            json.dump(log, file)
            file.write("\n")

    except Exception as e:
        print("Error: ", e)


def save_watered_time():
    """Save the last watered time to a JSON file and emit to server."""
    water_time = datetime.now()
    data = {
        "last_water": water_time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with open("watered_status.json", "w", encoding="utf-8") as file:
        json.dump(data, file)

    sio.emit("water_time", {
             "last_water": water_time.strftime("%Y-%m-%d %H:%M:%S")})


def get_watered_last_time():
    """Read last watering time from file if it exists and emit to server."""
    with open("watered_status.json", 'r', encoding='utf-8') as file:
        data = json.load(file)

    if data["last_water"] == "None":
        sio.emit("water_time", {"last_water": None})
        return

    sio.emit("water_time", {"last_water": data["last_water"]})


def get_expressions(window, previous_expressions, previous_need_water, previous_needs):
    """Update current expressions and manage transitions for watered state and new needs.
    
     This function:
    - Calls `expression_refresh` to get the latest expressions, needs and log.
    - Detects if watering just happened (based on previous_need_water and need_water).
    - Updates the global `expressions` list when there is a new state.
    - Triggers a display refresh signal when new expressions should start.
    - Saves a log entry asynchronously to a JSONL file.
    - Clears LLM text after a configurable timeout.
    - Schedules itself to run again after 5 seconds via QTimer.
    """
    global llm_response, new_expressions_signal, expressions, needs, loading, watered, water_index, sensors, \
        text_time

    # Εvaluate state based on latest sensor readings
    new_expressions, need_water, needs, log = expression_refresh(sensors)

    # Update visual expressions if there is no active LLM response
    if not llm_response:

        # Check if watering just happened
        if is_watered(previous_need_water, need_water):
            watered = True
            save_watered_time()
            water_index = 0
            expressions = new_expressions
        # New needs detected, update expressions
        elif previous_expressions is None or previous_needs != needs:
            expressions = new_expressions
            new_expressions_signal = True

            if loading:
                loading = False
                window.display_finished_signal.emit()

    #Add watered and spray status to log
    log["watered"] = watered
    log["water_spray"] = spray_status

    # Save log asynchronously so we don't block the UI
    threading.Thread(target=save_to_json, args=(log,), daemon=True).start()

    # Clear LLM text from the screen after TEXT_THRESHOLD has passed
    if text_time is not None and datetime.now() - text_time > TEXT_THRESHOLD:
        window.LlmTextDisplay("", False)
        text_time = None

    # Update info overlay with current sensor values
    window.InfoTextRefresh(info_format(sensors))

    # Schedule next evaluation in 5 seconds
    QTimer.singleShot(5000, lambda: get_expressions(
        window, expressions, need_water, needs))


def wait_response():
    """Wait for LLM response for a limited time, then show message."""
    global llm_response, text_time, new_response

    timeout = 40
    while timeout > 0:
        time.sleep(0.1)
        # If response finished or a new response started, stop waiting
        if not llm_response or new_response:
            if new_response:
                new_response = False

            return
        timeout -= 0.1

    # Timeout: no LLM response received
    llm_response = False
    text_time = datetime.now() - timedelta(minutes=1)
    window.llm_text_signal.emit("No LLM response", True)
    window.display_finished_signal.emit()


def display_signal():
    """Handle the signal from the Display widget to show the next expression or emoji.

    Two main modes:
    - Normal mode (llm_response == False):
        * Show watering "thank you" sequence if `watered` is True.
        * Otherwise, play through the current `expressions` list.
    - LLM response mode (llm_response == True):
        * First show a "talk" image while waiting.
        * Then play response_expressions (GIFs or emoji PNGs).
        * When finished, clear the LLM response flag and continue.
    """
    global new_expressions_signal, expressions, index, window, water_index, \
        water_expressions, watered, response_expressions, index_response, llm_response

    # Normal mood expressions
    if not llm_response:

        # --- Watering "thank you" sequence ---
        if watered:
            if water_index == 0:
                window.ExpressionRefresh(water_expressions[water_index], False)
                water_index += 1
            elif water_index == 1:
                window.ExpressionRefresh(water_expressions[water_index], False)
                water_index += 1
            elif water_index == 2:
                window.ExpressionRefresh(water_expressions[water_index], False)
                water_index += 1
            else:
                # Finished water sequence - continue with normal expressions
                watered = False
                window.signal = True
                new_expressions_signal = True
                QTimer.singleShot(1000, window.display_finished_signal.emit)

        # --- Normal expression sequence ---
        else:

            # If new expressions were set, restart from the beginning
            if new_expressions_signal:
                new_expressions_signal = False
                index = 0
            # If there are only one expression and we reached the end, refresh expressions
            elif not new_expressions_signal and len(needs) == 1 and index == len(expressions)+1:
                expressions, _, _, _ = expression_refresh(sensors)
                index = 0

            # Show next expression in the list
            if index < len(expressions):
                is_last = (index == len(expressions) - 1)

                try:
                    # Next expression and checks if it is the last one
                    if is_last and expressions[index].lower().endswith('.gif'):
                        window.ExpressionRefresh(expressions[index], False)
                    else:
                        window.ExpressionRefresh(expressions[index], is_last)
                except Exception as e:
                    # If an expression fails, try previous or same index as fallback
                    if index == 0:
                        print(
                            f"Error displaying expression {expressions[index]}: {e}")
                        window.ExpressionRefresh(expressions[index], is_last)
                    else:
                        print(
                            f"Error displaying expression {expressions[index]}: {e}")
                        window.ExpressionRefresh(expressions[index-1], is_last)

                index += 1

            # If we just finished the list and the last was a GIF - wait before refreshing
            elif index == len(expressions) and expressions[index-1].lower().endswith('.gif'):
                is_last = (index == len(expressions))
                window.signal = True
                QTimer.singleShot(
                    8000, lambda: window.display_finished_signal.emit())
                index += 1

            # Reached the end of expressions - restart from beginning
            else:
                index = 0
                is_last = (index == len(expressions) - 1)
                window.ExpressionRefresh(expressions[index], is_last)

    # LLM response expressions
    else:

        # Show "talk" image while waiting for LLM
        if window.signal and index_response == 0:
            window.llm_talk_signal.emit(talk())
            threading.Thread(target=wait_response, daemon=True).start()

        # Show LLM expressions/emojis response
        elif response_expressions and index_response < len(response_expressions):

            # If it is a gif expression
            if response_expressions[index_response].endswith('.gif'):
                window.ExpressionRefresh(
                    response_expressions[index_response], False)
                index_response += 1
                if index_response == len(response_expressions):
                    llm_response = False

            # If it is an emoji character
            else:
                emoji = get_emoji_png(response_expressions[index_response])

                if emoji is not None:
                    window.ExpressionRefresh(emoji, False)
                    index_response += 1
                    if index_response == len(response_expressions):
                        llm_response = False
                else:
                    index_response += 1
                    window.display_finished_signal.emit()
        # No more LLM expressions to show
        else:
            llm_response = False
            window.display_finished_signal.emit()


def get_emoji_png(emoji):
    """Map a unicode emoji character to a local PNG file (Noto Color Emoji set)"""
    unicode = "_".join(f"{ord(c):x}" for c in emoji)

    emoji_to_search = f"emoji_u{unicode}.png"
    path = os.path.join("notos_emojis", emoji_to_search)
    path = path.replace("\\", "/")

    if os.path.exists(path):
        return path
    else:
        return None


def read_sensor_data():
    """Continuously read sensor values and update the global sensors object.
    
    - Reads:
        * soil moisture from the ADC on the HAT board,
        * air humidity and temperature from the DHT22,
        * light (lux) from the VEML7700.
    """
    global sensors, hat_board

    # Initialize hardware sensors
    dhtdevice = adafruit_dht.DHT22(board.D0, use_pulseio=False)
    i2c = busio.I2C(board.SCL, board.SDA)
    veml7700 = adafruit_veml7700.VEML7700(i2c)
    hat_board = Board(1, 0x11)
    hat_board.set_adc_enable()
    time.sleep(2)

    last_soil = 0
    last_temp = 0

    
    while True:

        try:
            # Read sensor values
            soil_moisture_sensor = hat_board.get_adc_value(hat_board.A0)
            air_moisture_sensor = dhtdevice.humidity
            temp_sensor = dhtdevice.temperature
            light_sensor = veml7700.lux

            # Handle faulty readings by using last valid values
            if soil_moisture_sensor and (soil_moisture_sensor == 1023 or soil_moisture_sensor == 1279 or soil_moisture_sensor == 1535):
                soil_moisture_sensor = last_soil
            else:
                last_soil = soil_moisture_sensor 

            if temp_sensor and temp_sensor <= 0:
                temp_sensor = last_temp
            else:
                last_temp = temp_sensor

            # Initialize or update global SensorsReadings object
            if sensors is None:
                sensors = SensorsReadings([soil_moisture_sensor], air_moisture_sensor,
                                          temp_sensor, light_sensor)
            else:
                sensors.air_moisture = air_moisture_sensor
                sensors.temp = temp_sensor
                sensors.lux = light_sensor
                sensors.soil_moisture.append(soil_moisture_sensor)

                # Keep a short rolling history for soil moisture
                if len(sensors.soil_moisture) > 3:
                    sensors.soil_moisture.pop(0)

            time.sleep(4)

        except RuntimeError as error:
            print("Error: " + error.args[0])
            time.sleep(1)
            continue
        except Exception as error:
            dhtdevice.exit()
            raise error
    '''
    while True:
        with open("sensors.json", "r", encoding='utf-8') as file:
            data = json.load(file)

        if sensors is None:
            sensors = SensorsReadings([data.get("υγρασία_εδάφους")], data.get(
                "υγρασία"), data.get("θερμοκρασία"), data.get("φως"))
        else:
            sensors.air_moisture = data.get("υγρασία")
            sensors.temp = data.get("θερμοκρασία")
            sensors.lux = data.get("φως")
            sensors.soil_moisture.append(data.get("υγρασία_εδάφους"))

            if len(sensors.soil_moisture) > 10:
                sensors.soil_moisture.pop(0)

        time.sleep(10)
    '''


def get_spray_last_status():
    """Read the last spray time from file and update spray status accordingly."""
    global spray_status, spray_time

    with open("spray_status.json", 'r', encoding='utf-8') as file:
        data = json.load(file)

    if data.get("last_spray") == "None":
        spray_status = False
        spray_time = None
        last_spray_dt = None
        return

    last_spray_dt = datetime.strptime(data["last_spray"], "%Y-%m-%d %H:%M:%S")

    now = datetime.now()

    if now - last_spray_dt < THRESHOLDS.spray:
        spray_status = True
        spray_time = last_spray_dt
    else:
        spray_status = False
        spray_time = None


@sio.event
def connect():
    """
    Socket.IO connect event handler; send initial state to the server.
    
    - Requests weather from the server.
    - Sends last spray status if available.
    - Requests thresholds.
    - Notifies the UI that the server is connected.
    """
    sio.emit("send_weather")

    if spray_time is not None:
        sio.emit("spray_status", {
                 "spray_status": spray_time.strftime("%Y-%m-%d %H:%M:%S")})

    get_watered_last_time()

    get_thresholds()
    window.connection_signal.emit(True)
    print("Connected to server")


@sio.event
def disconnect():
    """
    Socket.IO disconnect event handler; reset local state and update UI.
    
    - Resets mood flags and pending requests.
    - Notifies the UI that the server is disconnected.
    """
    global angry_mood, sad_mood, request

    window.connection_signal.emit(False)
    angry_mood = False
    sad_mood = False
    request = None
    print("Disconnected from server")


def connect_to_server():
    """
    Background loop that maintains connection with the Flask server via Socket.IO.
    Uses a simple HMAC-based header for authentication, and retries every
    10 seconds if the connection fails.
    """
    global sio, window

    while True:
        try:

            nonce = str(int(time.time()))
            signature = hmac.new(API_KEY, nonce.encode(),
                                 hashlib.sha256).hexdigest()
            sio.connect("http://172.20.10.6:5000",
                        headers={"Authorization": f"{nonce}:{signature}"})
            break
        except Exception as e:
            window.connection_signal.emit(False)
            print(e)
            print("Connection error, retrying in 10 seconds...")
            time.sleep(10)

    sio.wait()


def get_thresholds():
    """Request current thresholds from the server."""
    sio.emit("get_thresholds")


def get_weather():
    """Periodically request weather information from the server."""
    global current_weather

    while True:
        try:
            if sio.connected:
                sio.emit("send_weather")
            else:
                current_weather = None
        except Exception as e:
            print(f"Error getting weather: {e}")
            time.sleep(1)

        time.sleep(1800)


if __name__ == "__main__":
    global window, sensors

    print("Starting application...")
    sensors = None
    get_spray_last_status()

    app = QApplication([])
    window = Display()

    # Start background thread that continuously updates sensor readings
    threading.Thread(target=read_sensor_data, daemon=True).start()

    # Wait until all sensors reading is available
    while sensors is None or sensors.temp is None:
        print("Waiting for sensor data...")
        time.sleep(1)

    # Start background tasks for random text, server connection and weather updates
    threading.Thread(target=show_random_text, daemon=True).start()
    threading.Thread(target=connect_to_server, daemon=True).start()
    threading.Thread(target=get_weather, daemon=True).start()

    # Connect signals and show UI
    window.display_finished_signal.connect(display_signal)
    window.show()
    app.setOverrideCursor(Qt.BlankCursor)

    # Start periodic expressions update loop
    get_expressions(window, None, False, None)

    sys.exit(app.exec_())
