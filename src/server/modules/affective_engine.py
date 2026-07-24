import math
import re
from modules import globals # type: ignore

def analyze_user_sentiment(text, client, llm_model_name):
    """Estrae il sentiment del testo usando il client LLM."""
    try:
        prompt = (
            "Analyze the sentiment of the following user message. "
            "Respond STRICTLY with a single float number between -1.0 (extremely negative/angry) "
            "and 1.0 (extremely positive/happy). Neutral messages must be 0.0. "
            "Do not include any text, reasoning, or markdown blocks. Only the raw number.\n\n"
            f"User message: '{text}'"
        )
        
        response = client.chat.completions.create(
            model=llm_model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10
        )
        
        output = response.choices[0].message.content.strip()
        match = re.findall(r"[-+]?\d*\.\d+|\d+", output)
        return float(match[0]) if match else 0.0
    except Exception as e:
        print(f"Error in LLM Sentiment Analysis: {e}")
        return 0.0

def update_plant_mood(sensor_data, user_sentiment, delta_time):
    """Calcola il vettore affettivo 2D e aggiorna lo stato globale."""
    
    # Calcolo Valence
    hardware_score = 1.0
    if sensor_data.get("need_watering", False): hardware_score -= 0.5
    if sensor_data.get("low_temp", False) or sensor_data.get("high_temp", False): hardware_score -= 0.3
    if sensor_data.get("low_humidity", False): hardware_score -= 0.2
    V_instant = (0.6 * hardware_score) + (0.4 * user_sentiment)
    
    # Calcolo Arousal
    f_interaction = math.exp(-delta_time / 60.0) if delta_time is not None else 0.5
    stress_intensity = (0.4 if sensor_data.get("need_watering", False) else 0.0) + \
                       (0.3 if (sensor_data.get("low_temp", False) or sensor_data.get("high_temp", False)) else 0.0)
    A_instant = (0.5 * f_interaction) + (0.5 * stress_intensity)
    A_instant = (A_instant * 2.0) - 1.0
    
    # Smoothing
    alpha_v = 0.45 if V_instant < globals.smoothed_valence else 0.20
    alpha_a = 0.30
    globals.smoothed_valence = (alpha_v * V_instant) + ((1.0 - alpha_v) * globals.smoothed_valence)
    globals.smoothed_arousal = (alpha_a * A_instant) + ((1.0 - alpha_a) * globals.smoothed_arousal)
    
    # Classificazione
    if globals.smoothed_valence > 0.1:
        current_mood = "Excited" if globals.smoothed_arousal > 0.0 else "Calm"
        globals.angry, globals.sad = False, False
    else:
        if globals.smoothed_arousal > 0.0:
            current_mood = "Anxious"
            globals.angry, globals.sad = True, False
        else:
            current_mood = "Lethargic"
            globals.angry, globals.sad = False, True
            
    return current_mood, V_instant, A_instant