# Session state
active_session_id = None
last_activity = None

# Hardware connectivity
rsb_connected = False
rsb_sid = None

# Random request state
random_watering = False
random_watering_time = None
random_spray = False
random_spray_time = None
request_key = None

# Logout and session management
cancel_logout = False
user_flag = False
new_user = False
user = None # The user dictionary

# Art Pipeline state
current_explanation = "No painting generated yet."
current_image_prompt = ""
current_medium = ""
current_canvas = ""
current_mapping = ""
previous_plant_state = None
smoothed_valence = 1.0
smoothed_arousal = 0.0
smoothed_wellbeing = 1.0
angry = False
sad = False

# Sensors and Environment
sensors_data = None
current_weather = None
change_threshold = False
thresholds_to_check = None

# Timing state
spray_status = None
watered_time = None

# Chat state
chat_history = []