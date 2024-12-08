import eventlet
import json
from flask import Flask, render_template
from flask_mqtt import Mqtt
from flask_socketio import SocketIO
from flask_bootstrap import Bootstrap
import os
from datetime import datetime
import sqlite3
import threading

eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'my secret key'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MQTT_BROKER_URL'] = 'broker.hivemq.com'
app.config['MQTT_BROKER_PORT'] = 1883
app.config['MQTT_USERNAME'] = ''
app.config['MQTT_PASSWORD'] = ''
app.config['MQTT_KEEPALIVE'] = 5
app.config['MQTT_TLS_ENABLED'] = False
app.config['DATABASE'] = 'sensor_data.db'

mqtt = Mqtt(app)
socketio = SocketIO(app)
bootstrap = Bootstrap(app)
db_lock = threading.Lock()

def init_db():
    with app.app_context():
        with sqlite3.connect(app.config['DATABASE']) as conn:
            cursor = conn.cursor()
            # Create tables for temperature and humidity readings
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sensor_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            print("Database initialized successfully")

def insert_reading(sensor_type, value):
    with db_lock:
        with sqlite3.connect(app.config['DATABASE']) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO sensor_readings (sensor_type, value) VALUES (?, ?)',
                (sensor_type, value)
            )
            conn.commit()

def get_latest_readings(limit=10):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT sensor_type, value, timestamp 
            FROM sensor_readings 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (limit,))
        return cursor.fetchall()

def get_latest_values():
    with sqlite3.connect(app.config['DATABASE']) as conn:
        cursor = conn.cursor()
        # Get latest temperature
        cursor.execute('''
            SELECT value, timestamp 
            FROM sensor_readings 
            WHERE sensor_type = 'temperature' 
            ORDER BY timestamp DESC 
            LIMIT 1
        ''')
        temp_result = cursor.fetchone()
        
        # Get latest humidity
        cursor.execute('''
            SELECT value, timestamp 
            FROM sensor_readings 
            WHERE sensor_type = 'humidity' 
            ORDER BY timestamp DESC 
            LIMIT 1
        ''')
        humid_result = cursor.fetchone()
        
        return {
            'temperature': temp_result if temp_result else (None, None),
            'humidity': humid_result if humid_result else (None, None)
        }

@app.route('/')
def index():
    try:
        latest_values = get_latest_values()
        latest_readings = get_latest_readings()
        return render_template('index.html', 
                             latest_values=latest_values,
                             latest_readings=latest_readings)
    except Exception as e:
        print(f"Error rendering template: {e}")
        return f"Error loading template: {str(e)}", 500

@mqtt.on_connect()
def handle_connect(client, userdata, flags, rc):
    print(f"\n[{datetime.now()}] Connected to MQTT Broker!")
    print("Subscribing to topics:")
    print("- mynode/Temperature")
    print("- mynode/Humidity")
    mqtt.subscribe('mynode/Temperature')
    mqtt.subscribe('mynode/Humidity')

@mqtt.on_message()
def handle_mqtt_message(client, userdata, message):
    timestamp = datetime.now()
    topic = message.topic
    payload = message.payload.decode()
    
    print(f"\n[{timestamp}] Message Received:")
    print(f"├── Topic: {topic}")
    print(f"├── Value: {payload}")
    
    try:
        value = float(payload)
        # Store in database based on topic
        if topic == 'mynode/Temperature':
            sensor_type = 'temperature'
        elif topic == 'mynode/Humidity':
            sensor_type = 'humidity'
        else:
            return
        
        # Insert into database
        insert_reading(sensor_type, value)
        print(f"├── Stored in database: {sensor_type} = {value}")
        
        # Get latest readings for all clients
        latest_values = get_latest_values()
        latest_readings = get_latest_readings()
        
        # Emit the updated data to all connected clients
        data = {
            'topic': topic,
            'value': value,
            'timestamp': timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            'latest_values': latest_values,
            'latest_readings': latest_readings
        }
        socketio.emit('mqtt_message', data=data)
        
    except ValueError as e:
        print(f"└── Error converting payload to float: {e}")

if __name__ == '__main__':
    init_db()  # Initialize database
    print(f"\n[{datetime.now()}] Starting Flask application...")
    print(f"├── Current working directory: {os.getcwd()}")
    print(f"└── Server running on http://0.0.0.0:5000")
    socketio.run(app, host='0.0.0.0', port=5000, use_reloader=False, debug=True)