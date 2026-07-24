# =====================================================================
# GLOBAL STATE MANAGER
# =====================================================================
# This file acts as the central nervous system of the application. 
# Because Flask and Socket.IO use multiple threads, we use this file 
# to share live state (like sensor data, user sessions, and mood) 
# across different modules without constantly pinging the database.
 
# --- 1. Session & Authentication State ---
active_session_id = None      # The ID of the currently connected user
last_activity = None          # Timestamp used to track inactivity for auto-logout

# --- 2. Hardware Connectivity State ---
rsb_connected = False         # Boolean flag tracking if the Raspberry Pi is online
rsb_sid = None                # The unique Socket.IO session ID for the hardware

# --- 3. Spontaneous Request Engine ---
random_watering = False       # Flag indicating if the plant spontaneously asked for water
random_watering_time = None   # Timestamp of the watering request
random_spray = False          # Flag indicating if the plant spontaneously asked for a misting
random_spray_time = None      # Timestamp of the misting request
request_key = None            # Firestore ID tracking the active random request

# --- 4. User Profile & Lifecycle Management ---
cancel_logout = False         # Flag to interrupt the auto-logout sequence
user_flag = False             # General boolean indicating if a user is actively chatting
new_user = False              # Flag to trigger the tutorial/onboarding UI
user = None                   # Dictionary holding the active user's Firestore profile data

# --- 5. Multimodal Art Pipeline & Affective Engine ---
current_explanation = "No painting generated yet."
current_image_prompt = ""
current_medium = ""
current_canvas = ""
current_mapping = ""
previous_plant_state = None   # Caches the previous state to detect if a new artwork should be generated

# 2D Circumplex Model Coordinates (Updated by affective_engine.py)
smoothed_valence = 1.0        # Plant's level of pleasure/well-being (X-axis)
smoothed_arousal = 0.0        # Plant's level of activation/stress (Y-axis)
smoothed_wellbeing = 1.0      
angry = False                 # Hardware mood flag mapping
sad = False                   # Hardware mood flag mapping

# --- 6. Sensor Telemetry & Environment ---
sensors_data = None           # Real-time dictionary payload from the Raspberry Pi sensors
current_weather = None        # Text description of local weather (e.g., 'Sunny', 'Rain')
change_threshold = False      # Dynamic flag that adjusts light thresholds if the weather is cloudy
thresholds_to_check = None    # Caches the current thresholds to detect if the JSON config was modified

# --- 7. Fulfillment Timers ---
spray_status = None           # Timestamp of the last successful leaf misting
watered_time = None           # Timestamp of the last successful soil watering

# --- 8. Conversational AI State ---
chat_history = []             # List of dictionaries maintaining the conversational context for the LLM