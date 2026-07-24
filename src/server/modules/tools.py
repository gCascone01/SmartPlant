import requests, pytz, json, random, time, os, threading # type: ignore
from astral import LocationInfo # type: ignore
from astral.sun import sun # type: ignore
from datetime import datetime, timedelta

from modules import globals # type: ignore

# =====================================================================
# SECTION 1: External APIs & Environmental Context
# =====================================================================
# Functions dealing with the physical world outside the plant (time of day, weather).

def is_day():
    """Calculates if it is currently daytime based on the sun's position in Greece."""
    
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
    
def fetch_weather(city):
    """Fetches current weather text (e.g., 'Sunny', 'Rain') from a public API."""
    
    try:
        response = requests.get(f"http://wttr.in/{city}?format=%C", timeout=15)
        return response.text if response.status_code == 200 else "unknown"
    except Exception as e:
        print(f"Error fetching weather: {e}")
        return "unknown"
    
def weather_worker(globals_obj, fetch_weather_func, interval=1700):
    """
    Background worker that continuously polls the weather API.
    Adjusts the 'change_threshold' flag if the weather is overcast or rainy,
    allowing the plant to tolerate lower light levels naturally.
    """

    while True:
        globals_obj.current_weather = fetch_weather_func("Agrinio")
        print(f"Weather updated: {globals_obj.current_weather}")
        if not any(word in globals_obj.current_weather.lower() for word in ["clear", "sun", "sunny", "bright"]):
            globals_obj.change_threshold = True
        else:
            globals_obj.change_threshold = False
        time.sleep(interval)
    
# =====================================================================
# SECTION 2: Plant Life-Support & Hardware Logic
# =====================================================================
# Functions evaluating sensor data and generating specific hardware requests.

def flower_state(data, THRESHOLDS, random_requests, socketio, db, update_user_func):
    """
    Evaluates raw sensor data against defined thresholds to generate human-readable status strings.
    Also triggers random spontaneous hardware requests (like asking for water or a leaf spray).
    """
    
    water = "- Watering: Do not need watering."
    air_humidity = "- Air humidity: Ideal.\n- Leaf spray: Do not need."
    light = "- Light: Ideal."
    temp = "- Temperature: Ideal."

    # Immediate critical needs
    if data["need_watering"]:
        if data["soil_moisture"] > THRESHOLDS.soil_moisture_min + 500:
            water = "- Watering: Yes, immediately!!"
        else:
            water = "- Watering: Yes, i need."

    # Random spontaneous watering request logic
    elif random_requests and globals.user["random_requests"] == False and globals.user["requests_fulfilled"] == False:
        if not globals.random_watering and (globals.watered_time is None or datetime.now() - globals.watered_time > timedelta(hours=1)) and data["soil_moisture"] >= 1900 and random.random() <= 0.5:
            water = "- Watering: Need a very small amount of water."
            globals.user["random_requests"] = True
            globals.random_watering = True
            globals.random_watering_time = datetime.now()
            threading.Thread(target=update_user_func, args=(db,), daemon=True).start()
            
            send_request("water", socketio, db)

    elif globals.random_watering:
        water = "- Watering: Need a very small amount of water."

    # Random spontaneous spray request logic
    elif random_requests and not globals.random_watering and globals.user["random_requests"] == False and globals.user["requests_fulfilled"] == False:
        if not globals.random_spray and (globals.spray_status is None or datetime.now() - globals.spray_status > timedelta(hours=1)):
            air_humidity = "- Air humidity: low. Needs spraying water on leaves!"
            globals.user["random_requests"] = True
            globals.random_spray = True
            globals.random_spray_time = datetime.now()
            threading.Thread(target=update_user_func, args=(db,), daemon=True).start()
            
            send_request("spray", socketio, db)

    elif globals.random_spray:
        air_humidity = "- Air humidity: low. Needs spraying water on leaves!"

    return water, light, temp, air_humidity

def send_request(req_type, socketio, db):
    """Logs a random hardware request (watering/spray) to Firestore and alerts the Raspberry Pi."""
    try:
        req = {
            "user": globals.user["user_id"],
            "request": req_type,
            "time": globals.random_spray_time if req_type == "spray" else globals.random_watering_time,
            "fulfilled": None
        }
        ref = db.collection("requests").document()
        ref.set(req)
        globals.request_key = ref.id
        socketio.emit("request", {"request": req_type})
    except Exception as e:
        print("Error sending request: ", e)

def send_flower_need(need, db, socketio, BASE_DIR, data=None):
    """
    Creates a dedicated Firestore document when the plant detects an environmental deficiency
    (e.g., 'hot', 'cold', 'low_light') and sends the tracking ID to the Raspberry Pi.
    """
    if globals.user and datetime.now() - globals.last_activity < timedelta(minutes=3):
        id = globals.user["user_id"]
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

def request_completed(request_type, db):
    """
    Marks the corresponding random request document as fulfilled in Firestore
    and resets the application's spontaneous request flags.
    """
    if request_type == "water" and globals.random_watering and datetime.now() - globals.random_watering_time < timedelta(minutes=3):
        globals.random_watering = False
        globals.random_watering_time = None
        req = db.collection("requests").document(globals.request_key)
        globals.request_key = None
        req.update({
            "fulfilled": datetime.now()
        })
    elif request_type == "spray":
        req = db.collection("requests").document(globals.request_key)
        globals.request_key = None
        globals.random_spray_time = None
        globals.random_spray = False
        req.update({
            "fulfilled": datetime.now()
        })

# =====================================================================
# SECTION 3: Configuration & State Watchers
# =====================================================================
# Functions managing the loading and continuous monitoring of JSON configs.

def initialize_thresholds(BASE_DIR, THRESHOLDS):
    """Loads plant environmental limit parameters from 'plant_thresholds.json' into memory."""
    config_path = os.path.join(BASE_DIR, 'config', 'plant_thresholds.json')
    with open(config_path, 'r', encoding='utf-8') as file:
        thresholds = json.load(file)

    if globals.change_threshold:
        thresholds["light_min"] = 100

    THRESHOLDS.soil_moisture_min = thresholds["soil_moisture_min"]
    THRESHOLDS.air_moisture_min = thresholds["air_moisture_min"]
    THRESHOLDS.temp_min = thresholds["temp_min"]
    THRESHOLDS.temp_max = thresholds["temp_max"]
    THRESHOLDS.light_min = thresholds["light_min"]
    THRESHOLDS.light_max = thresholds["light_max"]

def initialize_keys(BASE_DIR, KEYS_OBJ):
    """Loads previously active hardware need tracking keys from 'need_keys.json'."""
    config_path = os.path.join(BASE_DIR, 'config', 'need_keys.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            data_from_file = json.load(file)
            for k, v in data_from_file.items():
                setattr(KEYS_OBJ, k, v)
    except FileNotFoundError:
        print(f"Error: Το αρχείο 'need_keys.json' δεν βρέθηκε.")

def check_thresholds(BASE_DIR, globals_obj, socketio_obj, initialize_thresholds_func, THRESHOLDS, sleep_time=60):
    """
    Background daemon loop: Actively watches 'plant_thresholds.json' for manual edits.
    If changed, it reloads the data and instantly notifies the Raspberry Pi.
    """
    config_path = os.path.join(BASE_DIR, 'config', 'plant_thresholds.json')
    while True:
        with open(config_path, 'r', encoding='utf-8') as file:
            thresholds = json.load(file)

        if globals_obj.thresholds_to_check is None:
            globals_obj.thresholds_to_check = thresholds.copy()
        else:
            if globals_obj.change_threshold:
                thresholds["light_min"] = 100

            if thresholds != globals_obj.thresholds_to_check:
                globals_obj.thresholds_to_check = thresholds.copy()
                initialize_thresholds_func(BASE_DIR, THRESHOLDS)

                if globals_obj.rsb_connected:
                    socketio_obj.emit("thresholds_updated")

        socketio_obj.sleep(sleep_time)

# =====================================================================
# SECTION 4: Session Maintenance
# =====================================================================

def auto_clear(socketio):
    """Background task to safely clear user state and UI hardware triggers after 3 minutes of inactivity."""
    while True:
        if globals.user_flag and globals.user and globals.rsb_connected and globals.last_activity is not None and datetime.now() - globals.last_activity > timedelta(minutes=3):
            print("-> Auto clear after inactivity")
            socketio.emit("reset_mood")
            socketio.emit("log_out")
            globals.user_flag = False

        socketio.sleep(10)