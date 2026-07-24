import hmac, hashlib, json, os
from datetime import datetime, timedelta
from flask import request # type: ignore
from flask_socketio import disconnect # type: ignore
from dataclasses import asdict

from modules import globals # type: ignore
from modules.tools import send_flower_need, request_completed # type: ignore

def register_socket_events(socketio, db, BASE_DIR, KEYS, API_KEY):
    # =====================================================================
    # SECTION 1: Hardware & Client Connection Management
    # =====================================================================
    # These events handle the initial handshake, security validation via HMAC, 
    # and tracking whether the Raspberry Pi or a web browser is connecting/disconnecting.
    
    @socketio.on("connect")
    def handle_connect():
        """
        Socket.IO handler: Accepts Raspberry Pi and Web Browser connections.
        Validates the Raspberry Pi using an HMAC signature to ensure hardware authenticity.
        Saves the unique SID of the Raspberry to avoid confusing it with web clients.
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

            # Sync physical hardware mood indicators on successful connection
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
        """
        Socket.IO handler: Identifies which endpoint disconnected by comparing SIDs.
        Updates global connectivity flags if the hardware goes offline.
        """

        # If the disconnecting client is the Raspberry Pi...
        if request.sid == globals.rsb_sid:
            globals.rsb_connected = False
            globals.rsb_sid = None
            print("-> Raspberry Pi disconnected")
        # If the disconnecting client is a standard web browser...
        else:
            print("-> Web Browser disconnected")

    # =====================================================================
    # SECTION 2: Telemetry & Sensor Data Intake
    # =====================================================================
    # These events continuously receive data from the hardware sensors 
    # and update global variables for the rest of the application to read.

    @socketio.on("sensors_data")
    def get_flower_status(data):
        """Socket.IO handler: Receives the latest real-time sensor array data from the Raspberry Pi."""
        globals.sensors_data = data

    @socketio.on("spray_status")
    def refresh_spray_status(data):
        """Socket.IO handler: Updates the global timestamp of the last time the leaves were sprayed."""

        if data.get("spray_status") is not None:
            globals.spray_status = datetime.strptime(
                data.get("spray_status"), "%Y-%m-%d %H:%M:%S")
        else:
            globals.spray_status = None

    @socketio.on("water_time")
    def get_watered_time(data):
        """Socket.IO handler: Updates the global timestamp of the last time the plant was watered."""

        if data.get("last_water") is not None:
            globals.watered_time = datetime.strptime(
                data.get("last_water"), "%Y-%m-%d %H:%M:%S")
        else:
            globals.watered_time = None

    # =====================================================================
    # SECTION 3: Environmental Needs & Hardware Thresholds
    # =====================================================================
    # These events manage critical plant life-support requests, tracking when the plant 
    # lacks resources (water, heat, light) and when those needs are finally met.

    @socketio.on("send_need")
    def check_flower_need(data):
        """
        Socket.IO handler: The Raspberry Pi reports that a specific environmental need is active.
        
        Logic:
        - If we don't already have an active tracking key for that need, create a new
          'flower_needs' document in Firestore and store its ID.
        - Otherwise, send back the existing key to acknowledge we are tracking it.
        - Persist the KEYS state locally to 'need_keys.json' to survive server reboots.
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

            # Save tracking keys to the local JSON config
            config_path = os.path.join(BASE_DIR, 'config', 'need_keys.json')
            with open(config_path, 'w', encoding='utf-8') as file:
                data_to_write = asdict(KEYS)
                json.dump(data_to_write, file, indent=4,)

        except Exception as e:
            print("Error sending flower need")

    @socketio.on("need_fulfilled")
    def fulfilled_need(data):
        """
        Socket.IO handler: A previously reported need has been fulfilled by the user/environment.

        Updates the corresponding Firestore document:
        - Sets the 'fulfilled' timestamp.
        - Optionally logs the 'sprayed' boolean flag for low_humidity events.
        - Stores the ID of the user connected at the time of fulfillment.
        - Clears the active tracking key so new alerts can be generated in the future.
        """

        try:
            key = data.get("key")

            # Link the fulfillment to the active user if they intervened recently
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

            # Clear corresponding key from the active trackers
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

    @socketio.on("get_thresholds")
    def get_thresholds():
        """
        Socket.IO handler: The Raspberry Pi is requesting the latest environmental parameters.
        Reads the local JSON file and transmits the thresholds over WebSockets.
        """

        with open("plant_thresholds.json", 'r', encoding='utf-8') as file:
            file_thresholds = json.load(file)

        # Dynamic override based on weather
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

    # =====================================================================
    # SECTION 4: Miscellaneous Actions (Weather & Remote Commands)
    # =====================================================================

    @socketio.on("send_weather")
    def send_weather():
        """Socket.IO handler: Broadcasts current localized weather data to the Raspberry Pi."""

        if globals.current_weather is not None:
            socketio.emit("weather", globals.current_weather)
        else:
            socketio.emit("weather", globals.current_weather)

    @socketio.on("request_completed")
    def request_completed_received(data):
        """
        Socket.IO handler: The hardware confirms that a random remote request 
        (like physical watering or spraying) has been successfully executed.
        """
        request_completed(data.get("request"), db)