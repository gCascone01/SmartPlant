import requests # type: ignore
from astral import LocationInfo # type: ignore
from astral.sun import sun # type: ignore
from datetime import datetime, timedelta
import pytz # type: ignore
import json
import random, time, os
import threading
from modules import globals # type: ignore
from dataclasses import asdict

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
    
def fetch_weather(city):
    """Fetches current weather for a specified city."""
    try:
        response = requests.get(f"http://wttr.in/{city}?format=%C", timeout=15)
        return response.text if response.status_code == 200 else "unknown"
    except Exception as e:
        print(f"Error fetching weather: {e}")
        return "unknown"
    
def flower_state(data, THRESHOLDS, random_requests, socketio, db, update_user_func):
    """Determine the current state of the flower based on sensor data."""
    water = "- Watering: Do not need watering."
    air_humidity = "- Air humidity: Ideal.\n- Leaf spray: Do not need."
    light = "- Light: Ideal."
    temp = "- Temperature: Ideal."

    if data["need_watering"]:
        if data["soil_moisture"] > THRESHOLDS.soil_moisture_min + 500:
            water = "- Watering: Yes, immediately!!"
        else:
            water = "- Watering: Yes, i need."
    elif random_requests and globals.user["random_requests"] == False and globals.user["requests_fulfilled"] == False:
        if not globals.random_watering and (globals.watered_time is None or datetime.now() - globals.watered_time > timedelta(hours=1)) and data["soil_moisture"] >= 1900 and random.random() <= 0.5:
            water = "- Watering: Need a very small amount of water."
            globals.user["random_requests"] = True
            globals.random_watering = True
            globals.random_watering_time = datetime.now()
            threading.Thread(target=update_user_func, args=(db,), daemon=True).start()
            
            # ---> Called directly here inside tools.py!
            send_request("water", socketio, db)

    elif globals.random_watering:
        water = "- Watering: Need a very small amount of water."

    # (Keep light, temp, and air humidity checks here...)
    # For random spray:
    elif random_requests and not globals.random_watering and globals.user["random_requests"] == False and globals.user["requests_fulfilled"] == False:
        if not globals.random_spray and (globals.spray_status is None or datetime.now() - globals.spray_status > timedelta(hours=1)):
            air_humidity = "- Air humidity: low. Needs spraying water on leaves!"
            globals.user["random_requests"] = True
            globals.random_spray = True
            globals.random_spray_time = datetime.now()
            threading.Thread(target=update_user_func, args=(db,), daemon=True).start()
            
            # ---> Called directly here inside tools.py!
            send_request("spray", socketio, db)

    elif globals.random_spray:
        air_humidity = "- Air humidity: low. Needs spraying water on leaves!"

    return water, light, temp, air_humidity

def send_request(req_type, socketio, db):
    """Send a random watering/spray request to Firestore and notify Raspberry Pi."""
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

def weather_worker(globals_obj, fetch_weather_func, interval=1700):
    """Background worker for weather updates."""
    while True:
        globals_obj.current_weather = fetch_weather_func("Agrinio")
        print(f"Weather updated: {globals_obj.current_weather}")
        if not any(word in globals_obj.current_weather.lower() for word in ["clear", "sun", "sunny", "bright"]):
            globals_obj.change_threshold = True
        else:
            globals_obj.change_threshold = False
        time.sleep(interval)

def check_thresholds(BASE_DIR, globals_obj, socketio_obj, initialize_thresholds_func, sleep_time=60):
    """Background task: Monitor 'plant_thresholds.json' for changes and notify Raspberry Pi."""
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
                initialize_thresholds_func()

                if globals_obj.rsb_connected:
                    socketio_obj.emit("thresholds_updated")

        socketio_obj.sleep(sleep_time)

def send_flower_need(need, db, socketio, BASE_DIR, data=None):
    """
    Create a new 'flower_needs' document for a specific need
    and send its key back to the Raspberry.
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

def initialize_thresholds(BASE_DIR, THRESHOLDS):
    """Load thresholds from 'config/plant_thresholds.json' into the global THRESHOLDS object."""
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
    """Initialize KEYS dataclass from 'need_keys.json'."""
    config_path = os.path.join(BASE_DIR, 'config', 'need_keys.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            data_from_file = json.load(file)
            for k, v in data_from_file.items():
                setattr(KEYS_OBJ, k, v)
    except FileNotFoundError:
        print(f"Error: Το αρχείο 'need_keys.json' δεν βρέθηκε.")

def auto_clear(socketio):
    """Background task to auto clear user state after inactivity."""
    while True:
        if globals.user_flag and globals.user and globals.rsb_connected and globals.last_activity is not None and datetime.now() - globals.last_activity > timedelta(minutes=3):
            print("-> Auto clear after inactivity")
            socketio.emit("reset_mood")
            socketio.emit("log_out")
            globals.user_flag = False

        socketio.sleep(10)