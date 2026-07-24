# modules/routes.py
import os
import json
import threading
from datetime import datetime, timedelta
from flask import send_file, jsonify, render_template, request, session  # type: ignore
from modules import globals  # type: ignore
from modules.user_manager import name_check, user_info  # type: ignore

def register_routes(app, BASE_DIR, db, socketio, initialize_llm, auto_logout):
    
    @app.route('/get_art', methods=['GET'])
    def get_art():
        art_path = os.path.join(BASE_DIR, "assets", "art.png")
        if os.path.exists(art_path):
            return send_file(art_path, mimetype="image/png")
        return jsonify(error="No art piece compiled yet"), 404

    @app.route('/')
    def chat_html():
        """Serve main chat HTML page."""
        return render_template("flower.html")

    @app.route("/show_form", methods=['POST'])
    def to_show_form():
        """Check if the user should be prompted to fill in the feedback form."""
        if globals.user["messages"] >= 7 and not globals.user["form_submitted"]:
            return jsonify(status="form")
        return jsonify(status="ok")

    @app.route("/show_email", methods=['POST'])
    def to_show_email():
        """Check if the globals.user should be prompted to fill in their email."""
        if globals.user["sessions"] >= 2:
            email = globals.user.get("email")
            if not email:
                return jsonify(status="email")
        return jsonify(status="ok")

    @app.route("/user_form", methods=['POST'])
    def click_form_button():
        """Mark that the current user has submitted the feedback form."""
        try:
            user_doc = db.collection("users").document(globals.user["user_id"])
            user_doc.update({
                "form_submitted": True,
                "form_submitted_time": datetime.now()
            })
            globals.user["form_submitted"] = True
        except Exception as e:
            print("Error checking form: ", e)

    @app.route("/check_user", methods=['POST'])
    def check_user():
        """Check if a new user is trying to connect."""
        connect_time = datetime.now()
        data = request.get_json()
        user_id = data.get("user_id")

        try:
            if "session_id" not in session:
                session["session_id"] = user_id

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

            if globals.active_session_id is None:
                print("-> New user")
                socketio.emit("clear_request")
                globals.active_session_id = session["session_id"]
                user_info(globals.active_session_id, db, initialize_llm)
                globals.request_key = None
                globals.last_activity = connect_time
                globals.user_flag = True
                return jsonify(status="ok")

            if session['session_id'] == globals.active_session_id:
                print("-> Same user returned")
                user_info(globals.active_session_id, db, initialize_llm, returned=True)
                globals.cancel_logout = True
                globals.last_activity = connect_time
                globals.user_flag = True
                return jsonify(status="ok")
        except Exception as e:
            print("Error checking user: ", e)

        return jsonify(status="wait")

    @app.route('/check_new_user', methods=['POST'])
    def check_personality_selection():
        """Set a personality for the user."""
        try:
            if globals.new_user:
                globals.new_user = False
                return jsonify(user=True, mood=globals.user["mood"], messages=globals.user["messages"], session=globals.user["sessions"])
            else:
                return jsonify(user=False, mood=globals.user["mood"], messages=globals.user["messages"], session=globals.user["sessions"])
        except Exception as e:
            print("Error checking new user: ", e)

    @app.route('/end', methods=['POST'])
    def end():
        return jsonify(status="success")

    @app.route("/spray_button", methods=["POST"])
    def spray():
        if not globals.rsb_connected:
            return jsonify(status="no_connection")
        socketio.emit("spray")
        globals.last_activity = datetime.now()
        if globals.random_spray:
            globals.random_spray = False
        return jsonify(status="success")

    @app.route('/wait')
    def wait():
        return render_template("wait.html")

    @app.route('/exit')
    def exit():
        return render_template("exit.html")

    @app.route('/inactivity')
    def inactivity():
        return render_template("inactivity.html")

    @app.route('/logout', methods=['GET', 'POST'])
    def logout():
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
        data = request.get_json()
        user_id = data.get('user_id')
        if globals.user and user_id == globals.user["user_id"]:
            globals.last_activity = datetime.now()
            globals.cancel_logout = True
        return jsonify({'status': 'success'})

    @app.route('/to_logout', methods=['POST'])
    def timer_to_logout():
        try:
            data = request.get_json()
            user_id = data.get('user_id')
            if globals.user and user_id == globals.user["user_id"]:
                globals.cancel_logout = False
        except Exception as e:
            print("Error on logout timer: ", e)
        return jsonify({'status': 'success'})

    @app.route('/send_name', methods=['POST'])
    def get_username():
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