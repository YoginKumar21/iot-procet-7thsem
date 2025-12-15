import os
import cv2
import datetime
import threading
import time
import json
import pytz  # For accurate Indian Standard Time
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

# ==================== Initialization ====================

# 1. Text-to-Speech
try:
    tts_engine = pyttsx3.init()
    tts_engine.setProperty('rate', 170)
    print("✓ TTS Ready")
except:
    print("⚠️  TTS disabled")

# 2. YOLO Vision (Front Camera)
try:
    vision_model = YOLO('yolov8n.pt')
    print("✓ YOLO loaded")
except:
    vision_model = None
    print("⚠️  Vision disabled")

# 3. Firebase (Relays)
firebase_enabled = False
try:
    if not firebase_admin._apps:
        # Ensure 'firebase_key.json' is in your folder
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
    'connected': False,
    'tracking': False
}

mqtt_client = mqtt.Client(client_id="mech_server_" + str(int(time.time())))
mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.tls_set()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✓ Robot Connected (MQTT)")
        robot_state['connected'] = True
    else:
        robot_state['connected'] = False

mqtt_client.on_connect = on_connect

def mqtt_connect():
    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start()
    except Exception as e:
        print(f"⚠️ Robot connection failed: {e}")

threading.Thread(target=mqtt_connect, daemon=True).start()

def send_robot_command(direction):
    """Sends a movement command via MQTT."""
    if not robot_state['connected']: return False
    mqtt_client.publish(MQTT_CONTROL_TOPIC, json.dumps({"direction": direction}))
    return True

# ==================== Logic Functions ====================

def get_current_datetime():
    """Returns the current date and time specifically for India (IST)."""
    try:
        ist = pytz.timezone('Asia/Kolkata')
        now = datetime.datetime.now(ist)
        return now.strftime("%A, %B %d, %Y"), now.strftime("%I:%M %p")
    except:
        now = datetime.datetime.now()
        return now.strftime("%A, %B %d, %Y"), now.strftime("%I:%M %p")

# --- CUSTOM RESPONSES ---
CUSTOM_RESPONSES = {
    'babita': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'babita mam': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'who is babita': "She is a teacher at SMVITM and she takes IoT for you. SMVITM da papa da teacher!",
    'enca ullar': "Yān eḍḍe ulle",
    'enna ullar': "Yān eḍḍe ulle",
    'ninna pudar': "Enna pudar mech",
    'gopal': "The don of Belman, the father of Ammekunne!",
    'who is gopal': "The don of Belman, the father of Ammekunne!",
    'mech': "Yes boss, I am listening.",
    'who are you': "I am Mech, your personal robot assistant."
}

def check_custom_response(message):
    msg_lower = message.lower().strip()
    for trigger, response in CUSTOM_RESPONSES.items():
        if trigger in msg_lower:
            return response
    return None

# --- RELAY CONTROL (Firebase) ---
RELAY_MAP = {'one': 1, '1': 1, 'two': 2, '2': 2, 'three': 3, '3': 3, 'four': 4, '4': 4}

def control_lights(message):
    if not firebase_enabled or relay_ref is None: return None
    msg = message.lower()
    
    state = None
    if 'on' in msg: state = True
    elif 'off' in msg: state = False
    else: return None

    triggered = []

    # Check "Turn ALL on/off"
    if 'all' in msg:
        for i in range(1, 5): 
            try: relay_ref.child(f'relay{i}').set(state)
            except: pass
        return f"Turned {'on' if state else 'off'} ALL lights."

    # Check Specific (1, 2, 3, 4)
    for word, num in RELAY_MAP.items():
        if word in msg:
            try:
                relay_ref.child(f'relay{num}').set(state)
                triggered.append(str(num))
            except: pass
    
    if triggered:
        unique = sorted(list(set(triggered)))
        return f"Turned {'on' if state else 'off'} light {', '.join(unique)}"
    return None

# --- OBJECT TRACKING (Reversed Polarity) ---
def tracking_loop():
    """Runs in background. Finds object -> Moves robot."""
    print("▶ STARTING TRACKING LOOP")
    robot_state['tracking'] = True
    start_time = time.time()
    
    # Run for 15 seconds max (safety timeout)
    while robot_state['tracking'] and (time.time() - start_time < 15):
        if not vision_model: break
        
        try:
            cam = cv2.VideoCapture(0)
            ret, frame = cam.read()
            cam.release()
            if not ret: continue

            height, width, _ = frame.shape
            results = vision_model(frame, verbose=False)
            
            # Find largest object
            best_box = None
            max_area = 0

            for r in results:
                for box in r.boxes:
                    if float(box.conf[0]) < 0.5: continue
                    x1, y1, x2, y2 = box.xyxy[0]
                    area = (x2 - x1) * (y2 - y1)
                    if area > max_area:
                        max_area = area
                        best_box = box
            
            if best_box:
                x1, y1, x2, y2 = best_box.xyxy[0]
                obj_center_x = (x1 + x2) / 2
                obj_label = vision_model.names[int(best_box.cls[0])]

                deadzone_left = width * 0.35
                deadzone_right = width * 0.65
                
                # Turn Logic (Left/Right are standard)
                if obj_center_x < deadzone_left:
                    send_robot_command('left')
                    time.sleep(0.1)
                    send_robot_command('stop')
                    
                elif obj_center_x > deadzone_right:
                    send_robot_command('right')
                    time.sleep(0.1)
                    send_robot_command('stop')
                    
                else:
                    # FORWARD LOGIC (Reversed Polarity)
                    # If object is far, we want to go FORWARD.
                    # Because motors are reversed, we send 'backward'.
                    obj_height = y2 - y1
                    if obj_height < (height * 0.40):
                        print(f"⬆ Moving Physically Forward (Sending 'backward')")
                        send_robot_command('backward') 
                        time.sleep(0.3)
                        send_robot_command('stop')
                    else:
                        print(f"🛑 Stop (Close enough to {obj_label})")
            
            else:
                send_robot_command('stop')

            time.sleep(0.1)

        except Exception as e:
            print(f"Tracking error: {e}")
            break
    
    send_robot_command('stop')
    robot_state['tracking'] = False
    print("⏹ END TRACKING LOOP")

# ==================== MAIN CHAT ENDPOINT ====================

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    user_query = data.get('message', '').strip()
    q_lower = user_query.lower()
    
    if not user_query: return jsonify({"reply": "I didn't hear you."})
    print(f"\n💬 User: {user_query}")

    # --- PRIORITY 1: STOP EVERYTHING ---
    if 'stop' in q_lower:
        robot_state['tracking'] = False
        send_robot_command('stop')
        return jsonify({"reply": "Stopping everything."})

    # --- PRIORITY 2: CUSTOM RESPONSES ---
    custom_reply = check_custom_response(user_query)
    if custom_reply:
        return jsonify({"reply": custom_reply})

    # --- PRIORITY 3: RELAY CONTROL ---
    if any(k in q_lower for k in ['light', 'relay', 'turn on', 'turn off']):
        light_resp = control_lights(user_query)
        if light_resp: return jsonify({"reply": light_resp})

    # --- PRIORITY 4: FOLLOW OBJECT ---
    if any(k in q_lower for k in ['follow', 'track', 'go to']) and 'object' in q_lower:
        if not robot_state['tracking']:
            threading.Thread(target=tracking_loop).start()
            return jsonify({"reply": "Okay, tracking object now."})
        else:
            return jsonify({"reply": "I am already tracking."})

    # --- PRIORITY 5: MANUAL MOVEMENT (Reversed Polarity) ---
    if 'forward' in q_lower:
        # User says Forward -> Send 'backward' -> Robot moves Physically Forward
        send_robot_command('backward') 
        threading.Timer(2.0, send_robot_command, args=['stop']).start()
        return jsonify({"reply": "Moving forward"})
    
    if 'backward' in q_lower:
        # User says Backward -> Send 'forward' -> Robot moves Physically Backward
        send_robot_command('forward') 
        threading.Timer(2.0, send_robot_command, args=['stop']).start()
        return jsonify({"reply": "Moving backward"})

    if 'left' in q_lower:
        send_robot_command('left')
        threading.Timer(2.0, send_robot_command, args=['stop']).start()
        return jsonify({"reply": "Turning left"})

    if 'right' in q_lower:
        send_robot_command('right')
        threading.Timer(0.5, send_robot_command, args=['stop']).start() # 500ms
        return jsonify({"reply": "Turning right"})

    # --- PRIORITY 6: TIME / DATE ---
    if any(t in q_lower for t in ['time', 'date', 'today', 'clock']):
        d, t = get_current_datetime()
        if 'time' in q_lower or 'clock' in q_lower:
            return jsonify({"reply": f"The time is {t}."})
        return jsonify({"reply": f"Today is {d}."})

    # --- PRIORITY 7: AI + SEARCH ---
    search_context = ""
    if any(w in q_lower for w in ['search', 'news', 'who is', 'what is', 'weather']):
        web_data = DDGS().text(user_query, max_results=2)
        if web_data: search_context = f"\n\nSearch Results:\n{web_data}"
    
    try:
        response = ollama.chat(model='llama3.2', messages=[
            {'role': 'system', 'content': "You are Mech. Short answers."},
            {'role': 'user', 'content': f"{user_query}{search_context}"}
        ])
        return jsonify({"reply": response['message']['content']})
    except:
        return jsonify({"reply": "I'm having trouble thinking."})

if __name__ == '__main__':
    d, t = get_current_datetime()
    print(f"🚀 Mech System Online | {d} | {t}")
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)