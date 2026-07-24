from flask import request, jsonify, session # type: ignore
import threading
import json
from datetime import datetime
from modules import globals, ai_pipeline # type: ignore
from modules.affective_engine import analyze_user_sentiment, update_plant_mood # type: ignore
from modules.tools import flower_state # type: ignore

def register_chat_endpoint(app, socketio, client, llm_model_name, THRESHOLDS, random_requests, db, update_user, send_log, BASE_DIR, is_day):
    
    @app.route('/message', methods=['POST'])
    def flower_response():
        """Handle user message, process 2D circumplex affect vector, and update pipelines."""
        global chat

        if globals.active_session_id is None or session['session_id'] != globals.active_session_id:
            return jsonify(status="refresh")

        message = request.json['message']

        if not globals.rsb_connected:
            return jsonify(status="no_connection")

        # Calculate temporal delta before updating last_activity timestamp
        now_time = datetime.now()
        if globals.last_activity is not None:
            delta_time = (now_time - globals.last_activity).total_seconds()
        else:
            delta_time = None

        globals.last_activity = now_time
        socketio.emit("mood")

        try:
            data_ok = False
            timeout = 12
            while timeout > 0:
                socketio.sleep(0.1)
                if globals.sensors_data is not None:
                    sensors = globals.sensors_data
                    globals.sensors_data = None
                    data_ok = True
                    break
                timeout -= 0.1
        except Exception as e:
            print("Error getting sensors: ", e)

        if not data_ok:
            return jsonify(status="error")

        # ==================== ADVANCED AFFECTIVE COMPUTING PIPELINE ====================
        # 1. Extract lexical sentiment polarity via Transformer
        user_sentiment_score = analyze_user_sentiment(message, client, llm_model_name)
        
        # 2. Update continuous 2D coordinate systems and extract discrete mood label
        current_mood_label, V_instant, A_instant = update_plant_mood(sensors["sensor"], user_sentiment_score, delta_time)
        
        # 3. CONSOLE TELEMETRY LOGGING (Data Science Tracking)
        print("\n" + "="*70)
        print(f"[USER INPUT]      : '{message}'")
        print(f"[NLP POLARITY]    : Sentiment Score = {user_sentiment_score:.4f}")
        print(f"[TIME DELTA]      : {f'{delta_time:.2f}s' if delta_time else 'First message'}")
        print("-"*70)
        print(f"[VALENCE STAGE]   : Instant = {V_instant:.4f}  --->  Smoothed (EMA) = {globals.smoothed_valence:.4f}")
        print(f"[AROUSAL STAGE]   : Instant = {A_instant:.4f}  --->  Smoothed (EMA) = {globals.smoothed_arousal:.4f}")
        print("-"*70)
        print(f"[CIRCUMPLEX MOOD] : Active State = {current_mood_label}")
        print("="*70 + "\n")
        
        # 4. Sync physical hardware behaviors via Socket.IO
        if globals.angry:
            socketio.emit("angry_mode")
        elif globals.sad:
            socketio.emit("sad_mode")
        else:
            socketio.emit("reset_mood")
        # ===============================================================================

        try:
            state_res = flower_state(
                sensors["sensor"], 
                THRESHOLDS, 
                random_requests, 
                socketio, 
                db, 
                update_user
            )

            info = "<plant_state>\n"
            for status in state_res:
                info += status + "\n"

            globals.user["messages"] += 1
            threading.Thread(target=update_user, args=(db,),daemon=True).start()

            info += f"- Weather: {globals.current_weather if globals.current_weather is not None else 'unknown'}.\n"
            info += f"- Current Emotional Mood: {current_mood_label}.\n"
            info += f"- Affect Coordinates: Valence={globals.smoothed_valence:.2f}, Arousal={globals.smoothed_arousal:.2f}.\n"
            info += "- Datetime: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + ".\n"
            info += "- Is day: " + ("yes" if is_day() else "no") + ".\n"
            
            # FIX: Accesso sicuro al nome utente
            info += f"- The user's name is {globals.user.get('name', 'Unknown')}.\n"
            info += "</plant_state>\n"

            is_revealing = request.json.get('is_revealing', False)
            
            if is_revealing:
                info += (
                    "\n- CRITICAL LIVE CONTEXT: You are currently rendering and growing this new painting in real-time on the user's interface. "
                    "The capillary tissues and canvas veins are still expanding. If the user asks questions about this specific artwork in creation, "
                    "you can answer and provide information about its core conceptual meaning, but you MUST keep it very general, high-level, and abstract. "
                    "Do not provide grand, microscopic, or highly specific visual details yet, as the painting is still physically forming on their screen. "
                    "Acknowledge proudly that you are actively channeling your fluid dynamics into this ongoing biological growth process right now.\n"
                )

            # FIX: Passiamo lo stile e il soggetto alla pianta per dare contesto
            info += f"\n- VISUAL ELEMENTS: {globals.current_image_prompt}. (Style: {globals.current_medium}. Subject: {globals.current_canvas}).\n"
            info += f"\n- PAINTING EXPLANATION: {globals.current_explanation}\n"
            llm_input = info + "User input: " + message

            # ==================== CONDITIONED MULTIMODAL ART GENERATION (AI 1) ====================
            current_state = (state_res, globals.current_weather, current_mood_label)

            if current_state != globals.previous_plant_state:
                print("Plant affect vector shifted! Commencing art pipeline...")
                reveal_duration = ai_pipeline.calculate_reveal_duration(sensors["sensor"])
                
                # Use the new module
                art_data = ai_pipeline.generate_art(
                    client, llm_model_name, BASE_DIR, info, reveal_duration, socketio
                )
                
                if art_data:
                    globals.current_medium = art_data.get("medium_and_style", "")
                    globals.current_canvas = art_data.get("random_canvas_subject", "")
                    globals.current_mapping = art_data.get("metaphorical_mapping", "")
                    globals.current_image_prompt = art_data.get("image_prompt", "")
                    globals.current_explanation = art_data.get("explanation", "")
                    
                globals.previous_plant_state = current_state
                
        except Exception as e:
            print(f"CRITICAL ERROR prima del Dialogue Engine: {e}")
        # ========================================================================================

        # ==================== DIALOGUE ENGINE (AI 2) ====================
        try:
            prediction_cleaned, globals.chat_history = ai_pipeline.get_dialogue_response(
                client, llm_model_name, globals.chat_history, llm_input
            )
            
            try:
                prediction_dict = json.loads(prediction_cleaned)
            except json.JSONDecodeError:
                prediction_dict = prediction_cleaned

            # Log interaction
            threading.Thread(
                target=send_log, 
                args=(llm_input, None, prediction_cleaned, sensors), 
                daemon=True
            ).start()

            if not globals.rsb_connected:
                return jsonify(status="no_connection")

            socketio.emit("response", prediction_dict)
            return jsonify(status="success")
                
        except Exception as e:
            print(f"Error in Dialogue Engine: {e}")
            socketio.emit("response", "Sorry, I cannot talk right now.")
            return jsonify(status="success")
