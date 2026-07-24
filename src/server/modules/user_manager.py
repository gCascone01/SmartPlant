from datetime import datetime, timedelta
import random, re

from modules import globals # type: ignore

# =====================================================================
# SECTION 1: User Validation & Standard Updates
# =====================================================================

def name_check(name):
    """
    Validates the format of a user's inputted display name.
    Ensures only standard Latin letters and underscores are used to prevent 
    database injection or formatting errors in the LLM prompts.
    """
    return bool(re.fullmatch(r"[A-Za-z_]+", name))

def update_user(db):
    """
    Pushes routine, lightweight updates (like message counts and random request flags) 
    to the active user's profile in the Firestore database.
    Designed to be run as a background thread to prevent blocking the main chat loop.
    """
    try:
        user_info = db.collection("users").document(globals.user["user_id"])
        user_info.update({
            "messages": globals.user["messages"],
            "random_requests": globals.user["random_requests"],
        })
    except Exception as e:
        print("Error updating user: ", e)

# =====================================================================
# SECTION 2: User Initialization & Session Construction
# =====================================================================

def user_info(user_id, db, initialize_llm_callback, returned=False):
    """
    Core function for retrieving an existing user's profile or constructing a new one.
    It calculates session counts based on temporal gaps, assigns an initial plant mood, 
    and sets the starting points for the 2D affective circumplex model (Valence/Arousal).
    """
    try:
        user_col = db.collection("users").document(user_id)
        user_info_doc = user_col.get()

        # --- PATH A: Existing User Returning ---
        if user_info_doc.exists:
            user_data = user_info_doc.to_dict()
            last_login = user_data["last_login"].replace(tzinfo=None)

            # Determine if enough time has passed to qualify as a brand-new session (5 hours)
            request = (datetime.now() - last_login) <= timedelta(hours=2) and user_data.get("random_requests", False)
            sessions = user_data.get("sessions", 1)
            if datetime.now() - last_login > timedelta(hours=5):
                sessions += 1

            # Update the database with fresh login metrics 
            update_data = {
                "last_login": datetime.now(),
                "random_requests": request,
                "sessions": sessions,
            }
            if "form_submitted" not in user_data:
                update_data["form_submitted"] = False

            user_col.update(update_data)

            # Load updated profile into global memory
            globals.user = user_col.get().to_dict()
            globals.user["user_id"] = user_id
            globals.user["requests_fulfilled"] = False

            # Preset the affective circumplex coordinates based on the user's saved mood
            if globals.user["mood"] in ["Happy"]:
                globals.smoothed_valence, globals.smoothed_arousal = 1.0, 0.2
            elif globals.user["mood"] in ["Sad"]:
                globals.smoothed_valence, globals.smoothed_arousal = -0.4, -0.5
            else:
                globals.smoothed_valence, globals.smoothed_arousal = -0.5, 0.6

            # Fire the callback to inject the correct personality prompt into the LLM
            if not returned:
                initialize_llm_callback(globals.user["mood"])
        
        # --- PATH B: Brand New User Creation ---
        else:
            globals.new_user = True
            # Randomly assign a personality for the new user's relationship with the plant
            mood = random.choice(["Happy", "Grumpy", "Sad"])

            # Construct the default database profile
            user_col.set({
                "messages": 0,
                "mood": mood,
                "random_requests": False,
                "last_login": datetime.now(),
                "form_submitted": False,
                "sessions": 1,
            })

            # Load the new profile into global memory
            globals.user = user_col.get().to_dict()
            globals.user["user_id"] = user_id
            globals.user["mood"] = mood
            globals.user["requests_fulfilled"] = False

            # Initialize the affective circumplex coordinates for the new mood
            if mood == "Happy":
                globals.smoothed_valence, globals.smoothed_arousal = 1.0, 0.2
            elif mood == "Sad":
                globals.smoothed_valence, globals.smoothed_arousal = -0.4, -0.5
            else:
                globals.smoothed_valence, globals.smoothed_arousal = -0.5, 0.6

            initialize_llm_callback(globals.user["mood"])

    except Exception as e:
        print("Error on getting/set user info: ", e)