import os
import cv2
import datetime
import threading
import time
import json
import pytz  # NEW: For accurate Indian Standard Time
from flask import Flask, request, jsonify
from flask_cors import CORS
import firebase_admin
from firebase_admin import credentials, db
import ollama
from duckduckgo_search import DDGS
from ultralytics import YOLO
from dotenv import load_dotenv
import pyttsx3
import paho.mqtt.client as mqtt

# --- LOAD SECRETS ---
load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Global response cache
last_response = {"text": "", "timestamp": 0}

# ==================== Initialization ====================

# 1. Text-to-Speech
tts_enabled = False
try:
    tts_engine = pyttsx3.init()
    tts_engine.setProperty('rate', 170)
    tts_enabled = True
    print("✓ TTS Ready")
except:
    print("⚠️  TTS disabled")

# 2. YOLO Vision
try:
    vision_model = YOLO('yolov8n.pt')
    print("✓ YOLO loaded")
except:
    vision_model = None
    print("⚠️  Vision disabled")

# 3. Firebase
firebase_enabled = False
try:
    if not firebase_admin._apps:
        # Ensure firebase_key.json exists in the same folder
        cred = credentials.Certificate('firebase_key.json')
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://home-39238-default-rtdb.firebaseio.com/'
        })
        relay_ref = db.reference('/')
        firebase_enabled = True
        print("✓ Firebase connected")
except Exception as e:
    print(f"⚠️  Firebase offline: {e}")
    relay_ref = None

# 4. MQTT (Robot Control)
MQTT_BROKER = "32293062994244f39a533ed8dd686608.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "yogin"
MQTT_PASS = "Yogin@2004"
MQTT_CONTROL_TOPIC = "robot/control"
MQTT_STATUS_TOPIC = "bot/status"

robot_state = {
    'moving': False,
    'direction': 'stop',
    'connected': False,
    'last_sensor_reading': {'front': 999, 'left': 999, 'right': 999},
    'last_status': ''
}

mqtt_client = mqtt.Client(client_id="mech_server_" + str(int(time.time())))
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set()

# ==================== Logic Functions ====================

def get_current_datetime():
    """
    Returns the current date and time specifically for India (IST).
    """
    try:
        # Define India Timezone
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.datetime.now(ist)
        
        # Format date: Monday, December 15, 2025
        date_str = now.strftime("%A, %B %d, %Y")
        
        # Format time: 03:45 PM
        time_str = now.strftime("%I:%M %p")
        
        return date_str, time_str
    except Exception as e:
        # Fallback to system time if pytz fails
        print(f"Timezone error: {e}")
        now = datetime.datetime.now()
        return now.strftime("%A, %B %d, %Y"), now.strftime("%I:%M %p")

# --- CUSTOM RESPONSES (Strict Matching) ---
CUSTOM_RESPONSES = {
    'babita': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'babita mam': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'babita maam': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'who is babita': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'enca ullar': "Yān eḍḍe ulle",
    'enna ullar': "Yān eḍḍe ulle",
    'ninna pudar': "Enna pudar mech",
    'ninna pudar dāda': "Enna pudar mech",
    'gopal': "The don of Belman, the father of Ammekunne!",
    'who is gopal': "The don of Belman, the father of Ammekunne!",
    'mech': "Yes boss, I am listening.",
    'who are you': "I am Mech, your personal robot assistant."
}

def check_custom_response(message):
    """
    Checks if the message contains keywords from the custom list.
    Returns the exact string if found, else None.
    """
    msg_lower = message.lower().strip()
    
    # Check exact matches or partial containment
    for trigger, response in CUSTOM_RESPONSES.items():
        # strict check: if the trigger word is inside the message
        if trigger in msg_lower:
            return response
    return None

# --- MQTT Handlers ---
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✓ Robot Connected (MQTT)")
        robot_state['connected'] = True
        client.subscribe(MQTT_STATUS_TOPIC)
    else:
        robot_state['connected'] = False

def on_message(client, userdata, msg):
    try:
        status = msg.payload.decode('utf-8')
        robot_state['last_status'] = status
        # Simple parsing for sensors
        if '[F:' in status:
            parts = status.split()
            for p in parts:
                if 'F:' in p: robot_state['last_sensor_reading']['front'] = int(p.split(':')[1].replace(']',''))
                if 'L:' in p: robot_state['last_sensor_reading']['left'] = int(p.split(':')[1].replace(']',''))
                if 'R:' in p: robot_state['last_sensor_reading']['right'] = int(p.split(':')[1].replace(']',''))
    except:
        pass

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

def mqtt_connect():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"⚠️ Robot connection failed: {e}")

threading.Thread(target=mqtt_connect, daemon=True).start()

def send_robot_command(direction):
    if not robot_state['connected']: return False
    mqtt_client.publish(MQTT_CONTROL_TOPIC, json.dumps({"direction": direction}))
    return True

# --- Light Control ---
RELAY_NAMES = {'light1': 1, 'light one': 1, 'light2': 2, 'light two': 2}
def control_lights(message):
    if not firebase_enabled: return None
    msg = message.lower()
    if 'on' in msg: state = True
    elif 'off' in msg: state = False
    else: return None
    
    triggered = []
    for key, val in RELAY_NAMES.items():
        if key in msg:
            try:
                relay_ref.child(f'relay{val}').set(state)
                triggered.append(str(val))
            except: pass
            
    if triggered: return f"Turned {'on' if state else 'off'} light {','.join(triggered)}"
    return None

# --- Vision ---
def get_vision_analysis():
    if not vision_model: return "Vision system is offline"
    try:
        cam = cv2.VideoCapture(0)
        ret, frame = cam.read()
        cam.release()
        if not ret: return "Camera error"
        
        results = vision_model(frame, verbose=False)
        detected = []
        for r in results:
            for box in r.boxes:
                if float(box.conf[0]) > 0.5:
                    detected.append(vision_model.names[int(box.cls[0])])
        
        if not detected: return "I don't see anything specific."
        return f"I see: {', '.join(set(detected))}"
    except:
        return "Camera error"

# --- Web Search ---
def perform_web_search(query):
    try:
        print(f"🔍 Searching: {query}")
        results = DDGS().text(query, max_results=3)
        if results:
            return "\n".join([f"{r['title']}: {r['body']}" for r in results])
        return None
    except:
        return None

# ==================== MAIN CHAT ENDPOINT ====================

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_query = data.get('message', '').strip()
    
    if not user_query:
        return jsonify({"reply": "I didn't hear you."})
    
    print(f"\n💬 User: {user_query}")
    
    # ---------------------------------------------------------
    # PRIORITY 1: CUSTOM RESPONSES (STRICT)
    # ---------------------------------------------------------
    custom_reply = check_custom_response(user_query)
    if custom_reply:
        print(f"✨ Custom Reply Triggered: {custom_reply}")
        # Return IMMEDIATELY. Do not go to AI.
        return jsonify({"reply": custom_reply})

    # ---------------------------------------------------------
    # PRIORITY 2: DATE & TIME (STRICT SYSTEM DATA)
    # ---------------------------------------------------------
    # Normalize query for checking
    q_lower = user_query.lower()
    
    # Keywords specifically for time/date
    time_triggers = ['time', 'clock', 'what is the time', 'current time', 'tell me time']
    date_triggers = ['date', 'what is the date', 'today', 'what day', 'current date']
    
    is_time = any(t in q_lower for t in time_triggers)
    is_date = any(t in q_lower for t in date_triggers)
    
    if is_time or is_date:
        date_str, time_str = get_current_datetime()
        
        if is_time and is_date:
            reply = f"Today is {date_str} and it is {time_str}."
        elif is_time:
            reply = f"The current time is {time_str}."
        else:
            reply = f"Today's date is {date_str}."
            
        print(f"⏰ Time/Date Reply: {reply}")
        # Return IMMEDIATELY. Do not go to AI.
        return jsonify({"reply": reply})

    # ---------------------------------------------------------
    # PRIORITY 3: HARDWARE COMMANDS (Lights/Vision/Robot)
    # ---------------------------------------------------------
    
    # Lights
    light_resp = control_lights(user_query)
    if light_resp:
        return jsonify({"reply": light_resp})

    # Vision
    if any(w in q_lower for w in ['see', 'look', 'camera', 'vision']):
        return jsonify({"reply": get_vision_analysis()})

    # Robot Movement
    if 'forward' in q_lower:
        send_robot_command('forward')
        threading.Timer(2.0, send_robot_command, args=['stop']).start() # Auto stop
        return jsonify({"reply": "Moving forward"})
    if 'stop' in q_lower:
        send_robot_command('stop')
        return jsonify({"reply": "Stopping"})

    # ---------------------------------------------------------
    # PRIORITY 4: AI + WEB SEARCH (Everything else)
    # ---------------------------------------------------------
    
    search_context = ""
    # Only search if specifically asked or for "who/what" questions
    if any(w in q_lower for w in ['search', 'news', 'who is', 'what is', 'weather']):
        web_data = perform_web_search(user_query)
        if web_data:
            search_context = f"\n\nSearch Results:\n{web_data}"
    
    system_prompt = (
        "You are Mech, a robot assistant. "
        "Answer in 1-2 short sentences. "
        "Do not hallucinate facts. "
        "If you don't know, say so."
        "If search results are provided, use them."
    )

    full_prompt = f"User: {user_query}{search_context}"

    try:
        response = ollama.chat(
            model='llama3.2',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': full_prompt}
            ]
        )
        ai_reply = response['message']['content']
        return jsonify({"reply": ai_reply})
        
    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"reply": "I'm having trouble thinking right now."})

# ==================== RUN SERVER ====================
if __name__ == '__main__':
    d, t = get_current_datetime()
    print(f"🚀 Mech System Online | {d} | {t}")
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)