from datetime import datetime, timedelta
import random
import re
from modules import globals # type: ignore

def name_check(name):
    """Validate username: only Latin letters and underscore are allowed."""
    return bool(re.fullmatch(r"[A-Za-z_]+", name))

def update_user(db):
    """Update user information in Firestore."""
    try:
        user_info = db.collection("users").document(globals.user["user_id"])
        user_info.update({
            "messages": globals.user["messages"],
            "random_requests": globals.user["random_requests"],
        })
    except Exception as e:
        print("Error updating user: ", e)

def user_info(user_id, db, initialize_llm_callback, returned=False):
    """Retrieve or initialize user info from Firestore."""
    try:
        user_col = db.collection("users").document(user_id)
        user_info_doc = user_col.get()

        if user_info_doc.exists:
            user_data = user_info_doc.to_dict()
            last_login = user_data["last_login"].replace(tzinfo=None)

            request = (datetime.now() - last_login) <= timedelta(hours=2) and user_data.get("random_requests", False)
            sessions = user_data.get("sessions", 1)
            if datetime.now() - last_login > timedelta(hours=5):
                sessions += 1

            update_data = {
                "last_login": datetime.now(),
                "random_requests": request,
                "sessions": sessions,
            }
            if "form_submitted" not in user_data:
                update_data["form_submitted"] = False

            user_col.update(update_data)

            globals.user = user_col.get().to_dict()
            globals.user["user_id"] = user_id
            globals.user["requests_fulfilled"] = False

            if globals.user["mood"] in ["Happy"]:
                globals.smoothed_valence, globals.smoothed_arousal = 1.0, 0.2
            elif globals.user["mood"] in ["Sad"]:
                globals.smoothed_valence, globals.smoothed_arousal = -0.4, -0.5
            else:
                globals.smoothed_valence, globals.smoothed_arousal = -0.5, 0.6

            if not returned:
                initialize_llm_callback(globals.user["mood"])
        else:
            globals.new_user = True
            mood = random.choice(["Happy", "Grumpy", "Sad"])

            user_col.set({
                "messages": 0,
                "mood": mood,
                "random_requests": False,
                "last_login": datetime.now(),
                "form_submitted": False,
                "sessions": 1,
            })

            globals.user = user_col.get().to_dict()
            globals.user["user_id"] = user_id
            globals.user["mood"] = mood
            globals.user["requests_fulfilled"] = False

            if mood == "Happy":
                globals.smoothed_valence, globals.smoothed_arousal = 1.0, 0.2
            elif mood == "Sad":
                globals.smoothed_valence, globals.smoothed_arousal = -0.4, -0.5
            else:
                globals.smoothed_valence, globals.smoothed_arousal = -0.5, 0.6

            initialize_llm_callback(globals.user["mood"])

    except Exception as e:
        print("Error on getting/set user info: ", e)