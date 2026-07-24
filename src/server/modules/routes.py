import os
from datetime import datetime, timedelta
from flask import send_file, jsonify, render_template, request, session  # type: ignore

from modules import globals  # type: ignore
from modules.user_manager import name_check, user_info  # type: ignore

def register_routes(app, BASE_DIR, db, socketio, initialize_llm, auto_logout):

    # =====================================================================
    # SECTION 1: Asset Delivery & Core Template Rendering Routes
    # =====================================================================
    # These routes handle serving the frontend HTML pages and multimedia assets.
    
    @app.route('/get_art', methods=['GET'])
    def get_art():
        """Serves the latest generated artwork image asset."""

        art_path = os.path.join(BASE_DIR, "assets", "art.png")
        if os.path.exists(art_path):
            return send_file(art_path, mimetype="image/png")
        return jsonify(error="No art piece compiled yet"), 404

    @app.route('/')
    def chat_html():
        """Serves the main interactive plant chat HTML interface."""
        return render_template("flower.html")
    
    @app.route('/wait')
    def wait():
        """Serves the waiting screen template."""
        return render_template("wait.html")

    @app.route('/exit')
    def exit():
        """Serves the session exit page template."""
        return render_template("exit.html")

    @app.route('/inactivity')
    def inactivity():
        """Serves the inactivity timeout page template."""
        return render_template("inactivity.html")

    # =====================================================================
    # SECTION 2: User Initialization & Connection Handshake
    # =====================================================================
    # These routes manage new users arriving at the application and checking slot availability.

    @app.route("/check_user", methods=['POST'])
    def check_user():
        """
        Handles incoming user connection handshakes.
        Checks if the slot is free, handles auto-logout of previous idle users,
        and matches returning user sessions.
        """

        connect_time = datetime.now()
        data = request.get_json()
        user_id = data.get("user_id")

        try:
            if "session_id" not in session:
                session["session_id"] = user_id

            # Auto-logout previous user if their inactivity exceeds the allowed timeout
            if globals.active_session_id and \
                    globals.last_activity and \
                    connect_time - globals.last_activity > timedelta(seconds=auto_logout):
                print("-> Previous user Auto-Logout")
                globals.sad = False
                globals.angry = False
                globals.active_session_id = None
                globals.last_activity = None
                globals.random_watering = False
                globals.random_spray = False
                globals.request_key = None
                socketio.emit("clear_request")

            # Initialize session for a completely new user
            if globals.active_session_id is None:
                print("-> New user")
                socketio.emit("clear_request")
                globals.active_session_id = session["session_id"]
                user_info(globals.active_session_id, db, initialize_llm)
                globals.request_key = None
                globals.last_activity = connect_time
                globals.user_flag = True
                return jsonify(status="ok")

            # Resume session for the currently active returning user
            if session['session_id'] == globals.active_session_id:
                print("-> Same user returned")
                user_info(globals.active_session_id, db, initialize_llm, returned=True)
                globals.cancel_logout = True
                globals.last_activity = connect_time
                globals.user_flag = True
                return jsonify(status="ok")
        except Exception as e:
            print("Error checking user: ", e)

        # If someone else is active, tell the frontend to redirect to the wait screen
        return jsonify(status="wait")

    @app.route('/check_new_user', methods=['POST'])
    def check_personality_selection():
        """Retrieves and returns the user's saved state, chat history metrics, and initialized mood."""

        try:
            if globals.new_user:
                globals.new_user = False
                return jsonify(user=True, mood=globals.user["mood"], messages=globals.user["messages"], session=globals.user["sessions"])
            else:
                return jsonify(user=False, mood=globals.user["mood"], messages=globals.user["messages"], session=globals.user["sessions"])
        except Exception as e:
            print("Error checking new user: ", e)

    # =====================================================================
    # SECTION 3: Session Lifecycle & Activity Monitoring
    # =====================================================================
    # These routes monitor user presence and handle the disconnection process.

    @app.route('/logout', methods=['GET', 'POST'])
    def logout():
        """Clears user session flags, resets plant hardware states, and officially logs the user out."""

        print("-> User logout")
        globals.sad = False
        globals.angry = False
        globals.smoothed_wellbeing = 1.0

        if globals.rsb_connected:
            socketio.emit("reset_mood")
            socketio.emit("clear_request")
            socketio.emit("log_out")

        globals.user_flag = False
        globals.random_watering = False
        globals.random_spray = False
        return jsonify({'status': 'success'})

    @app.route('/reset_last_activity', methods=['POST'])
    def reset_last_activity():
        """Updates the internal timestamp whenever the user refocuses the browser tab to prevent timeout."""
        data = request.get_json()
        user_id = data.get('user_id')
        if globals.user and user_id == globals.user["user_id"]:
            globals.last_activity = datetime.now()
            globals.cancel_logout = True
        return jsonify({'status': 'success'})

    @app.route('/to_logout', methods=['POST'])
    def timer_to_logout():
        """Flags the active session as eligible for logout when the browser tab is hidden/minimized."""
        try:
            data = request.get_json()
            user_id = data.get('user_id')
            if globals.user and user_id == globals.user["user_id"]:
                globals.cancel_logout = False
        except Exception as e:
            print("Error on logout timer: ", e)
        return jsonify({'status': 'success'})
    
    # =====================================================================
    # SECTION 4
    # =====================================================================
    
    @app.route("/spray_button", methods=["POST"])
    def spray():
        """Spray button useful to communicate the plant that it has been watered (before the sensors can notice that)."""

        if not globals.rsb_connected:
            return jsonify(status="no_connection")
        socketio.emit("spray")
        globals.last_activity = datetime.now()
        if globals.random_spray:
            globals.random_spray = False
        return jsonify(status="success")

    @app.route('/send_name', methods=['POST'])
    def get_username():
        """Validates and saves the user's customized display name in both system memory and the database."""
        
        try:
            globals.last_activity = datetime.now()
            username = request.json['username']
            check = name_check(username)
            if check:
                globals.user["name"] = username
                user_col = db.collection("users").document(globals.user["user_id"])
                user_col.update({"username": username})
                return jsonify(status="success")
            else:
                return jsonify(status="error")
        except Exception as e:
            print("Error on getting name: ", e)