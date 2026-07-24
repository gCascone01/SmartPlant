import os
import json
import re
import threading
import requests # type: ignore
import time
from modules import globals # type: ignore

def generate_art(client, model_name, base_dir, info, reveal_duration, socketio):
    """Gestisce la pipeline di generazione artistica."""
    try:
        prompt_path = os.path.join(base_dir, "system_prompt", "new_llm_prompt.txt")
        with open(prompt_path, "r", encoding="utf-8") as file:
            art_system_instruction = file.read()

        # Input per l'AI
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

        # Avvia il task in background passando socketio
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
    """Task di background per chiamare l'API esterna e inviare l'evento via socketio."""
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
            
            # Notifica il client via WebSocket
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

def get_dialogue_response(client, model_name, chat_history, llm_input):
    """Gestisce l'interazione testuale con l'utente."""
    chat_history.append({"role": "user", "content": llm_input})

    # Ciclo di retry per robustezza (come avevi nel server)
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=chat_history,
                temperature=0.7
            )
            prediction = response.choices[0].message.content
            chat_history.append({"role": "assistant", "content": prediction})
            
            # Pulizia JSON se necessario
            prediction_cleaned = re.sub(r'^```json\s*|```$', '', prediction.strip(), flags=re.MULTILINE).strip()
            return prediction_cleaned, chat_history
            
        except Exception as e:
            if attempt == 2: raise e
            time.sleep(2)
            
    return "Sorry, I'm having trouble thinking.", chat_history

def calculate_reveal_duration(sensor_data):
    """
    Calcola la durata del Progressive Capillary Reveal (in ms) 
    in funzione dello stress termico e idrico della pianta.
    """
    base_duration = 180000  # 3 minuti (valore nominale)
    
    # Coefficiente di stress termico (Temperatura ottimale > 18°C)
    temp = sensor_data.get("temp", 22.0)
    low_temp = sensor_data.get("low_temp", False)
    high_temp = sensor_data.get("high_temp", False)
    
    if low_temp or temp < 18.0:
        # Rallentamento cinetico proporzionale al freddo (fino a +150%)
        alpha_T = 1.0 + min(1.5, (18.0 - temp) * 0.15)
    elif high_temp:
        alpha_T = 1.2
    else:
        alpha_T = 1.0
        
    # Coefficiente di stress idrico (soil_moisture)
    need_watering = sensor_data.get("need_watering", False)
    if need_watering:
        # Incremento del tempo in base alla severità dell'inaridimento
        alpha_M = 1.5
        if sensor_data.get("soil_moisture", 0) > 2300:
            alpha_M = 2.1
    else:
        alpha_M = 1.0
        
    # Calcolo del tempo totale combinato
    total_duration = int(base_duration * alpha_T * alpha_M)
    
    # Guardrail di sicurezza:bound tra 1.5 e 8 minuti
    return max(90000, min(480000, total_duration))

def initialize_llm(choice, socketio, base_dir):
    """Initialize LLM chat history with selected personality mood."""
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