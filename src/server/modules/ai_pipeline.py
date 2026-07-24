import os, json, re, threading, requests, time # type: ignore
from modules import globals # type: ignore

# =====================================================================
# SECTION 1: Artistic Pipeline & Generative Art Trigger
# =====================================================================

def generate_art(client, model_name, base_dir, info, reveal_duration, socketio):
    """
    Manages the generative art pipeline by loading the system instructions, 
    constructing the prompt with past context, and requesting a structured JSON response from the LLM.
    """
    
    try:
        prompt_path = os.path.join(base_dir, "system_prompt", "new_llm_prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as file:
            art_system_instruction = file.read()

        # Input for the AI combining current state and previous artwork context
        art_input = info + f"\n- Your PREVIOUS artwork was: {globals.current_explanation}\n- RULE: Pivot to a new subject."

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": art_system_instruction},
                {"role": "user", "content": art_input}
            ],
            temperature=0.95
        )

        content = re.sub(r'^```json\s*|```$', '', response.choices[0].message.content.strip(), flags=re.MULTILINE).strip()
        art_data = json.loads(content)

        # Launch background task to call the external image generation API
        threading.Thread(
            target=_run_image_generation_task, 
            args=(art_data, reveal_duration, base_dir, socketio), 
            daemon=True
        ).start()
        
        return art_data
    except Exception as e:
        print(f"Error in Art Pipeline: {e}")
        return None

def _run_image_generation_task(art_data, reveal_duration, base_dir, socketio):
    """
    Background task that calls the external image generation endpoint, 
    saves the resulting asset, and broadcasts the metadata via Socket.IO.
    """

    url = "http://150.140.142.76:9999/generate"
    payload = {
        "prompt": art_data.get("image_prompt", ""), 
        "height": 1024, 
        "width": 768, 
        "num_inference_steps": 20, 
        "guidance_scale": 10.0
    }
    
    try:
        res = requests.post(url, json=payload, timeout=120)
        if res.status_code == 200:
            save_path = os.path.join(base_dir, "assets", "art.png")
            with open(save_path, "wb") as f:
                f.write(res.content)
            
            # Notify the client via WebSocket with reveal duration (the time it will take the plant to "paint") and metaphorical mapping
            socketio.emit("new_art_available", {
                "duration": reveal_duration,
                "medium": art_data.get("medium_and_style", ""),
                "canvas": art_data.get("random_canvas_subject", ""),
                "mapping": art_data.get("metaphorical_mapping", ""),
                "explanation": art_data.get("explanation", "")
            })
            print("Artwork generated and notification sent via SocketIO.")
    except Exception as e:
        print(f"Error in Image Gen: {e}")

# =====================================================================
# SECTION 2: Dialogue Engine & Conversational Processing
# =====================================================================

def get_dialogue_response(client, model_name, chat_history, llm_input):
    """
    Manages text-based interactions and dialogue history with the plant.
    It sends the current user input along with the accumulated chat history to the LLM,
    and appends the LLM's response back to the chat history for context in future interactions.
    """
    chat_history.append({"role": "user", "content": llm_input})

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=chat_history,
                temperature=0.7
            )
            prediction = response.choices[0].message.content
            chat_history.append({"role": "assistant", "content": prediction})
            
            prediction_cleaned = re.sub(r'^```json\s*|```$', '', prediction.strip(), flags=re.MULTILINE).strip()
            return prediction_cleaned, chat_history
            
        except Exception as e:
            if attempt == 2: raise e
            time.sleep(2)
            
    return "Sorry, I'm having trouble thinking.", chat_history

# =====================================================================
# SECTION 3: Reveal Kinetics & Plant Personality Initialization
# =====================================================================

def calculate_reveal_duration(sensor_data):
    """
    Calculates the duration of the Progressive Capillary Reveal (in milliseconds) 
    dynamically based on the plant's thermal and water stress levels.
    """
    base_duration = 180000  # 3 minutes 
    
    # Thermal stress coefficient (optimal temperature > 18°C)
    temp = sensor_data.get("temp", 22.0)
    low_temp = sensor_data.get("low_temp", False)
    high_temp = sensor_data.get("high_temp", False)
    
    if low_temp or temp < 18.0:
        # Kinetic slowdown proportional to cold conditions (up to +150%)
        alpha_T = 1.0 + min(1.5, (18.0 - temp) * 0.15)
    elif high_temp:
        alpha_T = 1.2
    else:
        alpha_T = 1.0
        
    # Water stress coefficient (soil moisture levels)
    need_watering = sensor_data.get("need_watering", False)
    if need_watering:
        # Increment in duration based on the severity of drought
        alpha_M = 1.5
        if sensor_data.get("soil_moisture", 0) > 2300:
            alpha_M = 2.1
    else:
        alpha_M = 1.0
        
    # Total combined duration calculation
    total_duration = int(base_duration * alpha_T * alpha_M)
    
    # Safety guardrails: bounded between 1.5 and 8 minutes
    return max(90000, min(480000, total_duration))

def initialize_llm(choice, socketio, base_dir):
    """Initializes the LLM chat history and sets global mood parameters based on the personality."""
    if choice in ["Χαρούμενο", "Happy"]:
        globals.angry = False
        globals.sad = False
        socketio.emit("reset_mood")
    elif choice in ["Γκρινιάρικο", "Grumpy"]:
        globals.angry = True
        globals.sad = False
        socketio.emit("angry_mode")
    elif choice in ["Λυπημένο", "Sad"]:
        globals.angry = False
        globals.sad = True
        socketio.emit("sad_mode")

    prompt_path = os.path.join(base_dir, "system_prompt", "llm_prompt_v2.txt")
    with open(prompt_path, 'r', encoding='utf-8') as file:
        llm_prompt = file.read()

    globals.chat_history = [{"role": "system", "content": llm_prompt}]