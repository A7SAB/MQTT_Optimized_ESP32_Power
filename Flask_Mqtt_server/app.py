import eventlet
eventlet.monkey_patch()

import json
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_mqtt import Mqtt
from flask_socketio import SocketIO
from flask_bootstrap import Bootstrap
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import sqlite3
import threading
import schedule
import time as time_module
from datetime import datetime, timedelta


app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['MQTT_BROKER_URL'] = 'broker.hivemq.com'
app.config['MQTT_BROKER_PORT'] = 1883
app.config['MQTT_USERNAME'] = ''
app.config['MQTT_PASSWORD'] = ''
app.config['MQTT_KEEPALIVE'] = 5
app.config['MQTT_TLS_ENABLED'] = False
app.config['DATABASE'] = 'sensor_data.db'

# Initialize extensions
mqtt = Mqtt(app)
socketio = SocketIO(app)
bootstrap = Bootstrap(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
db_lock = threading.Lock()

# Database Functions
def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

def init_db():
    with app.app_context():
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Create tables
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sensor_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT UNIQUE NOT NULL,
                    location TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    sensor_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT UNIQUE NOT NULL,
                    sleep_duration INTEGER NOT NULL DEFAULT 30,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pumps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pump_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    location TEXT,
                    tank_shape TEXT NOT NULL,
                    tank_length REAL,
                    tank_width REAL,
                    tank_height REAL,
                    tank_diameter REAL,
                    status TEXT DEFAULT 'pending',
                    last_reading REAL,
                    last_update TIMESTAMP,
                    is_running BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            #Schduling record tables
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pump_id TEXT NOT NULL,
                    schedule_date TEXT NOT NULL,
                    schedule_time TEXT NOT NULL,
                    duration INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pump_id) REFERENCES pumps(pump_id)
                )
            ''')


            #Tables for automation rules
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pump_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pump_id TEXT NOT NULL,
                    sensor_id TEXT NOT NULL,
                    threshold_value REAL NOT NULL,
                    reading_type TEXT NOT NULL DEFAULT 'temperature',  -- Added comma here
                    comparison_type TEXT NOT NULL,  -- 'above' or 'below'
                    action TEXT NOT NULL,  -- 'on' or 'off'
                    duration INTEGER NOT NULL,  -- duration in minutes
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (pump_id) REFERENCES pumps(pump_id),
                    FOREIGN KEY (sensor_id) REFERENCES sensor_locations(device_id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rule_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER NOT NULL,
                    sensor_value REAL NOT NULL,
                    action_taken TEXT NOT NULL,
                    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (rule_id) REFERENCES pump_rules(id)
                )
            ''')

            # Add default admin user if not exists
            cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',))
            if not cursor.fetchone():
                password_hash = generate_password_hash('admin123')
                cursor.execute(
                    'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                    ('admin', password_hash)
                )

            conn.commit()


# User Management
class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

def get_user_by_username(username):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user_data = cursor.fetchone()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password_hash'])
    return None

@login_manager.user_loader
def load_user(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password_hash'])
    return None

# Routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = get_user_by_username(username)
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))

# Core functionality routes
@app.route('/dashboard')
@login_required
def dashboard():
    sensors = get_all_sensors()
    locations = get_locations()
    latest_readings = get_sensor_readings(limit=10)
    return render_template('dashboard.html', sensors=sensors, locations=locations, latest_readings=latest_readings)

@app.route('/analytics')
@login_required
def analytics():
    return render_template('analytics.html')

@app.route('/sensor-management')
@login_required
def sensor_management():
    return render_template('sensor_management.html')

@app.route('/discovery')
@login_required
def discovery():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ds.device_id, ds.last_seen,
                       sr.sensor_type, sr.value, sr.timestamp
                FROM device_settings ds
                LEFT JOIN (
                    SELECT device_id, MAX(timestamp) as max_time
                    FROM sensor_readings
                    GROUP BY device_id
                ) latest ON ds.device_id = latest.device_id
                LEFT JOIN sensor_readings sr ON latest.device_id = sr.device_id 
                    AND latest.max_time = sr.timestamp
                LEFT JOIN sensor_locations sl ON ds.device_id = sl.device_id
                WHERE sl.device_id IS NULL
                GROUP BY ds.device_id
            ''')
            unclaimed_sensors = cursor.fetchall()
            return render_template('sensor_discovery.html', unclaimed_sensors=unclaimed_sensors)
    except Exception as e:
        print(f"Error loading unclaimed sensors: {str(e)}")
        flash('Error loading unclaimed sensors')
        return render_template('sensor_discovery.html', unclaimed_sensors=[])
    

@app.route('/pump')
@login_required
def pump_management():
    return render_template('pump_management.html')


# API Routes
@app.route('/api/sensors/claim', methods=['POST'])
@login_required
def claim_sensor():
    """Assign a discovered sensor to a location"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data received'}), 400
            
        device_id = data.get('device_id')
        location = data.get('location')
        name = data.get('name')
        
        if not all([device_id, location, name]):
            return jsonify({
                'success': False, 
                'message': 'Missing required fields'
            }), 400
        
        with get_db() as conn:
            cursor = conn.cursor()
            # First check if sensor exists and is unclaimed
            cursor.execute('''
                SELECT 1 FROM device_settings ds
                LEFT JOIN sensor_locations sl ON ds.device_id = sl.device_id
                WHERE ds.device_id = ? AND sl.device_id IS NULL
            ''', (device_id,))
            
            if not cursor.fetchone():
                return jsonify({
                    'success': False, 
                    'message': 'Sensor not found or already claimed'
                }), 400
            
            # Add location information
            cursor.execute('''
                INSERT INTO sensor_locations (device_id, location, name)
                VALUES (?, ?, ?)
            ''', (device_id, location, name))
            conn.commit()
            
            return jsonify({
                'success': True, 
                'message': 'Sensor claimed successfully'
            })
            
    except Exception as e:
        print(f"Error claiming sensor: {str(e)}")
        return jsonify({
            'success': False, 
            'message': f'Error claiming sensor: {str(e)}'
        }), 500

@app.route('/api/get-readings')
@login_required
def get_readings():
    sensor_id = request.args.get('sensor', 'all')
    readings = get_sensor_readings(sensor_id)
    return jsonify({'success': True, 'readings': readings})



# Autmation Rouets
@app.route('/api/pump/<pump_id>/rules', methods=['GET'])
@login_required
def get_pump_rules(pump_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT pr.*, sl.name as sensor_name, sl.location as sensor_location
            FROM pump_rules pr
            JOIN sensor_locations sl ON pr.sensor_id = sl.device_id
            WHERE pr.pump_id = ?
            ORDER BY pr.created_at DESC
        ''', (pump_id,))
        rules = [dict(row) for row in cursor.fetchall()]
        return jsonify(rules)

@app.route('/api/pump/<pump_id>/rules', methods=['POST'])
@login_required
def add_pump_rule(pump_id):
    data = request.get_json()
    required_fields = ['sensor_id', 'reading_type', 'comparison_type', 
                      'threshold_value', 'action', 'duration']
    
    if not all(field in data for field in required_fields):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if data['reading_type'] not in ['temperature', 'moisture']:
        return jsonify({'error': 'Invalid reading type'}), 400

    # Add the rule to database
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO pump_rules (
                pump_id, sensor_id, reading_type, threshold_value,
                comparison_type, action, duration
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (pump_id, data['sensor_id'], data['reading_type'],
              data['threshold_value'], data['comparison_type'],
              data['action'], data['duration']))
        conn.commit()
        return jsonify({'success': True, 'id': cursor.lastrowid})
    
@app.route('/api/pump/rule/<rule_id>', methods=['DELETE'])
@login_required
def delete_pump_rule(rule_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM pump_rules WHERE id = ?', (rule_id,))
        conn.commit()
        return jsonify({'success': True})

@app.route('/api/pump/rule/<rule_id>/toggle', methods=['POST'])
@login_required
def toggle_rule(rule_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pump_rules 
            SET is_active = NOT is_active 
            WHERE id = ?
        ''', (rule_id,))
        conn.commit()
        return jsonify({'success': True})

@app.route('/api/pump/<pump_id>/rule-history', methods=['GET'])
@login_required
def get_rule_history(pump_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT ra.*, pr.sensor_id, pr.threshold_value, 
                    pr.comparison_type, sl.name as sensor_name
            FROM rule_actions ra
            JOIN pump_rules pr ON ra.rule_id = pr.id
            JOIN sensor_locations sl ON pr.sensor_id = sl.device_id
            WHERE pr.pump_id = ?
            ORDER BY ra.executed_at DESC
            LIMIT 50
        ''', (pump_id,))
        history = [dict(row) for row in cursor.fetchall()]
        return jsonify(history)

# Add function to check sensor readings against rules
def check_pump_rules(sensor_id, reading_type, value):
    """
    Evaluate active pump rules for a given sensor reading and possibly
    turn a pump on or off, respecting the conditions below:

      - If pump is already ON and an 'on' command is triggered: ignore & record error.
      - If pump is ON and an 'off' command is triggered: turn OFF immediately (no scheduling).
      - If pump is OFF and the water level is below 10%: ignore & record error if 'on' is requested.
      - If an 'off' command is triggered, do so immediately (absolute off, no time scheduling).

    sensor_id: The ID of the sensor location (device_id) that triggered the rule.
    reading_type: e.g., "water_level", "temperature", etc.
    value: Numeric sensor value.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Find all active rules matching sensor_id, reading_type
        cursor.execute('''
            SELECT * FROM pump_rules
            WHERE sensor_id = ?
              AND reading_type = ?
              AND is_active = TRUE
        ''', (sensor_id, reading_type))
        rules = cursor.fetchall()

        for rule in rules:
            # Determine if rule should trigger
            if rule['comparison_type'] == 'above':
                should_trigger = value > rule['threshold_value']
            else:
                should_trigger = value < rule['threshold_value']

            if not should_trigger:
                # If the condition isn't met, do nothing for this rule
                continue

            # Rule triggered: log evaluation
            print(f"[RULE TRIGGERED] Rule ID: {rule['id']} - Sensor ID: {sensor_id} - Value: {value}")

            # ------------------------------------------------------------------
            # 1) Fetch the current pump status from DB
            # ------------------------------------------------------------------
            cursor.execute('''
                SELECT is_running
                  FROM pumps
                 WHERE pump_id = ?
            ''', (rule['pump_id'],))
            pump_row = cursor.fetchone()

            # If pump not found or invalid, continue or handle as error
            if not pump_row:
                print(f"[ERROR] Pump {rule['pump_id']} not found in DB.")
                continue

            is_running = pump_row['is_running']  # Boolean: True/False

            # ------------------------------------------------------------------
            # 2) Check the rule action: 'on' or 'off'
            # ------------------------------------------------------------------
            if rule['action'] == 'on':
                # Condition A: Pump already ON?
                if is_running:
                    # Record error: pump is already ON, ignoring turn-on command
                    print(f"[ERROR] Pump {rule['pump_id']} is already ON. Ignoring turn-on command.")
                    cursor.execute('''
                        INSERT INTO rule_actions (rule_id, sensor_value, action_taken)
                        VALUES (?, ?, ?)
                    ''', (rule['id'], value, 'error: pump_already_on'))
                    conn.commit()
                    continue

                # Condition B: Pump is OFF, but water level < 10%?
                # (Assuming reading_type == "water_level" and we treat < 10 as <10%)
                # Adjust the numeric threshold as needed for your logic.
                if reading_type.lower() == 'water_level' and value < 10:
                    print(f"[ERROR] Water level too low ({value}%). Ignoring turn-on command for pump {rule['pump_id']}.")
                    cursor.execute('''
                        INSERT INTO rule_actions (rule_id, sensor_value, action_taken)
                        VALUES (?, ?, ?)
                    ''', (rule['id'], value, 'error: water_level_too_low'))
                    conn.commit()
                    continue

                # Otherwise, turn pump ON & schedule turn OFF if a duration is set
                print(f"Turning ON pump {rule['pump_id']}")

                # Record the action
                cursor.execute('''
                    INSERT INTO rule_actions (rule_id, sensor_value, action_taken)
                    VALUES (?, ?, ?)
                ''', (rule['id'], value, 'on'))
                
                # Update pump status
                cursor.execute('''
                    UPDATE pumps
                       SET is_running = TRUE,
                           last_update = CURRENT_TIMESTAMP
                     WHERE pump_id = ?
                ''', (rule['pump_id'],))
                conn.commit()

                # Publish ON command
                control_msg = {
                    'device_id': rule['pump_id'],
                    'command': 'on',
                    'timestamp': datetime.now().isoformat()
                }
                topic = f'mynode/{rule["pump_id"]}/control'
                print(f"[MQTT] Publishing ON command to topic: {topic}, Message: {control_msg}")
                mqtt.publish(topic, json.dumps(control_msg), qos=1)

                # Schedule turn OFF after duration (in minutes)
                # Only schedule if the duration > 0. If you prefer to always schedule, remove the check.
                if rule['duration'] > 0:
                    duration_seconds = rule['duration'] * 60
                    timer = threading.Timer(duration_seconds, turn_off_pump, [rule['pump_id']])
                    timer.start()
                    print(f"[TIMER] Turn OFF scheduled for pump {rule['pump_id']} in {duration_seconds} seconds.")
            
            elif rule['action'] == 'off':
                # If the pump is already OFF, there's nothing to do
                if not is_running:
                    
                    #print(f"Pump {rule['pump_id']} is already OFF. Ignoring turn-off command.")
                    # Optionally record an error or an info action
                    #cursor.execute('''
                     #   INSERT INTO rule_actions (rule_id, sensor_value, action_taken)
                      #  VALUES (?, ?, ?)
                    #''', (rule['id'], value, 'info: pump_already_off'))
                    #conn.commit()
                    
                    continue

                # If the pump is ON, turn it off immediately (no scheduling)
                print(f"Turning OFF pump {rule['pump_id']} immediately.")
                cursor.execute('''
                    INSERT INTO rule_actions (rule_id, sensor_value, action_taken)
                    VALUES (?, ?, ?)
                ''', (rule['id'], value, 'off'))
                cursor.execute('''
                    UPDATE pumps
                       SET is_running = FALSE,
                           last_update = CURRENT_TIMESTAMP
                     WHERE pump_id = ?
                ''', (rule['pump_id'],))
                conn.commit()

                off_msg = {
                    'device_id': rule['pump_id'],
                    'command': 'off',
                    'timestamp': datetime.now().isoformat()
                }
                # Publish OFF command (absolute off)
                mqtt.publish(f'mynode/pump_control', json.dumps(off_msg), qos=1)

            # If other actions or logic exist, handle them here.
            # End of for-loop (rules)

def turn_off_pump(pump_id):
    """Turn off the pump after the scheduled duration ends."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pumps
               SET is_running = FALSE,
                   last_update = CURRENT_TIMESTAMP
             WHERE pump_id = ?
        ''', (pump_id,))
        conn.commit()

    off_msg = {
        'device_id': pump_id,
        'command': 'off',
        'timestamp': datetime.now().isoformat()
    }
    # Publish an OFF command to the relevant MQTT topic
    mqtt.publish(f'mynode/pump_control', json.dumps(off_msg), qos=1)
    print(f"[TIMER] Pump {pump_id} turned off after scheduled duration.")


@app.route('/api/delete-sensor', methods=['POST'])
@login_required
def delete_sensor():
    try:
        data = request.get_json()
        device_id = data.get('device_id')
        
        with get_db() as conn:
            cursor = conn.cursor()
            # Delete sensor settings and readings
            cursor.execute('DELETE FROM device_settings WHERE device_id = ?', (device_id,))
            cursor.execute('DELETE FROM sensor_readings WHERE device_id = ?', (device_id,))
            cursor.execute('DELETE FROM sensor_locations WHERE device_id = ?', (device_id,))
            conn.commit()
            
        return jsonify({'success': True, 'message': 'Sensor deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    

@app.route('/api/set-sleep-time', methods=['POST'])
@login_required
def set_sleep_time():
    try:
        data = request.get_json()
        sleep_time = data.get('sleep_time')
        device_id = data.get('device_id')
        
        if not device_id or not isinstance(sleep_time, (int, float)) or sleep_time < 1:
            return jsonify({'success': False, 'message': 'Invalid parameters'}), 400

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE device_settings 
                SET sleep_duration = ?, last_seen = CURRENT_TIMESTAMP 
                WHERE device_id = ?
            ''', (sleep_time, device_id))
            
            if cursor.rowcount == 0:
                cursor.execute('''
                    INSERT INTO device_settings (device_id, sleep_duration)
                    VALUES (?, ?)
                ''', (device_id, sleep_time))
            
            conn.commit()

        mqtt.publish(f'mynode/{device_id}/config/sleep', json.dumps({
            'device_id': device_id,
            'sleep_time': sleep_time
        }))
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Helper functions
def get_all_sensors():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                d.device_id,
                d.sleep_duration as sleep_time,
                t.value as temperature,
                h.value as moisture,
                COALESCE(d.last_seen, t.timestamp, h.timestamp) as last_update,
                sl.name,
                sl.location
            FROM device_settings d
            LEFT JOIN sensor_locations sl ON d.device_id = sl.device_id
            LEFT JOIN (
                SELECT device_id, value, timestamp
                FROM sensor_readings
                WHERE sensor_type = 'temperature'
                AND timestamp IN (
                    SELECT MAX(timestamp)
                    FROM sensor_readings
                    WHERE sensor_type = 'temperature'
                    GROUP BY device_id
                )
            ) t ON d.device_id = t.device_id
            LEFT JOIN (
                SELECT device_id, value, timestamp
                FROM sensor_readings
                WHERE sensor_type = 'moisture'
                AND timestamp IN (
                    SELECT MAX(timestamp)
                    FROM sensor_readings
                    WHERE sensor_type = 'moisture'
                    GROUP BY device_id
                )
            ) h ON d.device_id = h.device_id
        ''')
        return [dict(row) for row in cursor.fetchall()]

def get_sensor_readings(sensor_id=None, limit=10):
    """Get sensor readings with location information"""
    with get_db() as conn:
        cursor = conn.cursor()
        if sensor_id and sensor_id != 'all':
            cursor.execute('''
                SELECT 
                    sr.device_id,
                    sr.sensor_type,
                    sr.value,
                    sr.timestamp,
                    sl.name,
                    sl.location,
                    ds.sleep_duration as sleep_time
                FROM sensor_readings sr
                LEFT JOIN sensor_locations sl ON sr.device_id = sl.device_id
                LEFT JOIN device_settings ds ON sr.device_id = ds.device_id
                WHERE sr.device_id = ?
                ORDER BY sr.timestamp DESC
                LIMIT ?
            ''', (sensor_id, limit))
        else:
            cursor.execute('''
                SELECT 
                    sr.device_id,
                    sr.sensor_type,
                    sr.value,
                    sr.timestamp,
                    sl.name,
                    sl.location,
                    ds.sleep_duration as sleep_time
                FROM sensor_readings sr
                LEFT JOIN sensor_locations sl ON sr.device_id = sl.device_id
                LEFT JOIN device_settings ds ON sr.device_id = ds.device_id
                ORDER BY sr.timestamp DESC
                LIMIT ?
            ''', (limit,))
        
        # Convert rows to dictionaries
        return [dict(row) for row in cursor.fetchall()]

def get_locations():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT location FROM sensor_locations')
        return [row[0] for row in cursor.fetchall()]

# MQTT Auth Handler
def handle_sensor_request(device_id, data):
    """Handle authentication requests for sensors"""
    with get_db() as conn:
        cursor = conn.cursor()
        try:
            # Handle sensor authentication
            cursor.execute('''
                INSERT OR IGNORE INTO device_settings 
                (device_id, sleep_duration, last_seen)
                VALUES (?, 30, CURRENT_TIMESTAMP)
            ''', (device_id,))
            
            conn.commit()
            
            # Send approval response
            response = {
                'device_id': device_id,
                'status': 'approved'
            }
            mqtt.publish('mynode/auth', json.dumps(response))
            print(f"[AUTH] Sent approval to sensor: {device_id}")
            
        except Exception as e:
            print(f"[ERROR] Database error in auth: {str(e)}")
            conn.rollback()
            raise

# MQTT handlers
@mqtt.on_connect()
def handle_connect(client, userdata, flags, rc):
    """
    Handle MQTT connection and subscribe to topics.
    """
    connection_status = {
        0: "Connected successfully",
        1: "Connection refused - incorrect protocol version",
        2: "Connection refused - invalid client identifier",
        3: "Connection refused - server unavailable",
        4: "Connection refused - bad username or password",
        5: "Connection refused - not authorized"
    }
    
    print(f"[MQTT] Connection status: {connection_status.get(rc, f'Unknown error code: {rc}')}")
    
    if rc != 0:
        print("[MQTT] Connection failed! Check broker settings and network connection")
        return
        
    try:
        # Topic list with separate auth channels for sensors and pumps
        topics = [
            # Sensor topics
            ('mynode/auth', 0),                  # Sensor auth
            ('mynode/Temperature', 0),           # Temperature readings
            ('mynode/moisture', 0),              # Moisture readings
            ('mynode/default/config/sleep', 0),  # Sensor sleep config
            
            # Pump topics
            ('mynode/pump_auth', 0),             # Pump auth channel
            ('mynode/water_level', 0),           # Water level readings
            ('mynode/pump_status', 0),           # Pump status updates
            ('mynode/pump_control', 0)           # Pump control commands
        ]
        
        # Subscribe to each topic
        for topic, qos in topics:
            try:
                result, mid = client.subscribe(topic, qos)
                if result == 0:
                    print(f"[MQTT] Subscribed to: {topic} (MID: {mid})")
                else:
                    print(f"[MQTT] Failed to subscribe to: {topic} (Result: {result})")
            except ValueError as e:
                print(f"[MQTT] Invalid topic filter: {topic} - {str(e)}")
            except Exception as e:
                print(f"[MQTT] Error subscribing to {topic}: {str(e)}")
        
        print("[MQTT] Topic subscription completed")
        
    except Exception as e:
        print(f"[MQTT] Error during connection setup: {str(e)}")
        import traceback
        traceback.print_exc()

def handle_sleep_config(device_id, action, data):
    """Handle sleep time configuration requests"""
    if action != 'get_sleep_time':
        return
        
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT sleep_duration FROM device_settings WHERE device_id = ?',
            (device_id,)
        )
        result = cursor.fetchone()
        sleep_time = result[0] if result else 30

        cursor.execute('''
            UPDATE device_settings 
            SET last_seen = CURRENT_TIMESTAMP 
            WHERE device_id = ?
        ''', (device_id,))
        conn.commit()

    response = {
        'device_id': device_id,
        'sleep_time': sleep_time
    }
    mqtt.publish('mynode/default/config/sleep', json.dumps(response))
    print(f"[SLEEP] Sent sleep time {sleep_time} to device: {device_id}")


def handle_sensor_data(topic, device_id, data):
    sensor_type = topic.split('/')[-1].lower()
    value = data.get(sensor_type.lower())
    
    if value is not None:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO sensor_readings (device_id, sensor_type, value)
                VALUES (?, ?, ?)
            ''', (device_id, sensor_type, float(value)))
            
            cursor.execute('''
                UPDATE device_settings 
                SET last_seen = CURRENT_TIMESTAMP 
                WHERE device_id = ?
            ''', (device_id,))
            
            conn.commit()

        #Check the preset rules by the user 
        print(f"[SENSOR] Calling check_pump_rules for {device_id}, Type: {sensor_type}, Value: {value}")
        check_pump_rules(device_id, sensor_type, float(value))


        # Send acknowledgment
        mqtt.publish('mynode/ack', json.dumps({
            'device_id': device_id,
            'status': 'received'
        }))
        print(f"[DATA] Acknowledged {sensor_type} reading from {device_id}")

        # Emit to websocket clients
        socketio.emit('mqtt_message', {
            'device_id': device_id,
            'topic': topic,
            'value': value,
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        
#pump_stuff
pump_readings = {}
pending_pumps = {}


def handle_water_level(pump_id, data):
    """Handle and store water level readings"""
    try:
        # Extract reading from different possible field names
        reading = None
        for field in ['reading', 'water_level', 'value']:
            if field in data and data[field] is not None:
                try:
                    reading = float(data[field])
                    break
                except (TypeError, ValueError):
                    continue
                
        if reading is None:
            print(f"[ERROR] No valid reading found in data: {data}")
            return

        timestamp = datetime.now()
        if 'timestamp' in data:
            try:
                timestamp = datetime.fromisoformat(data['timestamp'])
            except (ValueError, TypeError):
                print(f"[WARNING] Invalid timestamp format, using current time")

        print(f"[PUMP] Processing reading: {reading} for pump: {pump_id}")
            
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Update pump's last reading and status
            cursor.execute('''
                UPDATE pumps 
                SET last_reading = ?,
                    last_update = ?,
                    status = CASE 
                        WHEN status = 'pending' THEN 'pending'
                        ELSE 'configured'
                    END
                WHERE pump_id = ?
            ''', (reading, timestamp, pump_id))
            
            conn.commit()

            # Get updated pump info
            cursor.execute('SELECT * FROM pumps WHERE pump_id = ?', (pump_id,))
            pump = cursor.fetchone()
            
            if pump:
                # Calculate volume and emit update via websocket
                volume_info = calculate_volume(pump_id)
                if volume_info:
                    socketio.emit('pump_reading', {
                        'pump_id': pump_id,
                        'reading': reading,
                        'timestamp': timestamp.isoformat(),
                        'volume': volume_info,
                        'is_running': bool(pump['is_running']),
                        'status': pump['status']
                    })
                    print(f"[PUMP] Emitted reading update for {pump_id}")

    except Exception as e:
        print(f"[ERROR] Error handling water level reading: {str(e)}")
        import traceback
        traceback.print_exc()

def handle_pump_status(pump_id, data):
    try:
        if pump_id not in pump_readings:
            pump_readings[pump_id] = {}
            
        pump_readings[pump_id].update({
            'is_running': data.get('status') == 'on',
            'last_active': data.get('timestamp', datetime.now().isoformat())
        })
        
        print(f"[PUMP] Updated status for {pump_id}: {data.get('status')}")
        
    except Exception as e:
        print(f"[ERROR] Error handling pump status: {str(e)}")


def calculate_volume(pump_id):
    """Calculate water volume and percentage for a pump based on last_reading"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get pump info directly from pumps table
            cursor.execute('''
                SELECT * FROM pumps WHERE pump_id = ?
            ''', (pump_id,))
            
            result = cursor.fetchone()
            
            if not result or result['last_reading'] is None:
                logging.warning(f"[VOLUME] No data available for pump {pump_id}")
                return None
                
            water_level = float(result['last_reading'])
            max_height = float(result['tank_height'] or 100)
            
            # Calculate volume based on tank shape
            volume = None
            try:
                if result['tank_shape'] == 'box':
                    if not all([result['tank_length'], result['tank_width']]):
                        logging.warning(f"[VOLUME] Missing box dimensions for pump {pump_id}")
                        return None
                        
                    volume = (float(result['tank_length']) * 
                            float(result['tank_width']) * 
                            (max_height - water_level)) / 1000  # Convert to liters
                            
                elif result['tank_shape'] == 'cylinder':
                    if not result['tank_diameter']:
                        logging.warning(f"[VOLUME] Missing cylinder dimensions for pump {pump_id}")
                        return None
                        
                    radius = float(result['tank_diameter']) / 2
                    volume = (3.14159 * radius * radius * 
                            (max_height - water_level)) / 1000  # Convert to liters
                            
            except (TypeError, ValueError) as e:
                logging.error(f"[VOLUME] Error calculating volume: {str(e)}")
                return None
            
            # Calculate percentage (inverted as water_level is distance from sensor)
            try:
                percentage = ((max_height - water_level) / max_height) * 100
                percentage = max(0, min(100, percentage))  # Clamp between 0 and 100
            except (TypeError, ValueError) as e:
                logging.error(f"[VOLUME] Error calculating percentage: {str(e)}")
                return None
            
            return {
                'volume': round(volume, 2) if volume is not None else None,
                'percentage': round(percentage, 1),
                'water_level': round(water_level, 1),
                'max_height': round(max_height, 1),
                'tank_shape': result['tank_shape']
            }
            
    except Exception as e:
        logging.error(f"[ERROR] Error calculating volume for pump {pump_id}: {str(e)}")
        return None

# API Routes
@app.route('/api/pumps', methods=['GET'])
def get_pumps():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pumps ORDER BY created_at DESC')
        return jsonify([dict(pump) for pump in cursor.fetchall()])

@app.route('/api/pending-pumps')
def get_pending_pumps():
    current_time = datetime.now()
    to_remove = []
    
    for pump_id, data in pending_pumps.items():
        pump_time = datetime.fromisoformat(data['timestamp'])
        if (current_time - pump_time).total_seconds() > 300:
            to_remove.append(pump_id)
    
    for pump_id in to_remove:
        del pending_pumps[pump_id]
        
    return jsonify(list(pending_pumps.keys()))


@app.route('/api/pump/<pump_id>/readings')
@login_required
def get_pump_reading_history(pump_id):
    """Get historical readings for a specific pump"""
    try:
        limit = request.args.get('limit', 100, type=int)
        readings = get_pump_readings(pump_id, limit)
        return jsonify({
            'success': True,
            'readings': readings
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/pumps/readings')
@login_required
def get_all_pump_readings():
    """Get latest readings for all pumps"""
    try:
        readings = get_latest_pump_readings()
        return jsonify({
            'success': True,
            'readings': readings
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def get_pump_readings(pump_id=None, limit=100):
    """Get historical pump readings"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        if pump_id:
            cursor.execute('''
                SELECT sr.*, p.name, p.location, p.tank_shape,
                       p.tank_height, p.tank_length, p.tank_width, p.tank_diameter
                FROM sensor_readings sr
                JOIN pumps p ON sr.device_id = p.pump_id
                WHERE sr.device_id = ?
                  AND sr.sensor_type = 'water_level'
                ORDER BY sr.timestamp DESC
                LIMIT ?
            ''', (pump_id, limit))
        else:
            cursor.execute('''
                SELECT sr.*, p.name, p.location, p.tank_shape,
                       p.tank_height, p.tank_length, p.tank_width, p.tank_diameter
                FROM sensor_readings sr
                JOIN pumps p ON sr.device_id = p.pump_id
                WHERE sr.sensor_type = 'water_level'
                ORDER BY sr.timestamp DESC
                LIMIT ?
            ''', (limit,))
            
        return [dict(row) for row in cursor.fetchall()]
    
def get_latest_pump_readings():
    """Get latest readings for all pumps"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            WITH LatestReadings AS (
                SELECT device_id, 
                       MAX(timestamp) as max_timestamp
                FROM sensor_readings
                WHERE sensor_type = 'water_level'
                GROUP BY device_id
            )
            SELECT sr.*, p.name, p.location, p.tank_shape,
                   p.tank_height, p.tank_length, p.tank_width, p.tank_diameter,
                   p.is_running, p.last_update
            FROM sensor_readings sr
            JOIN LatestReadings lr 
                ON sr.device_id = lr.device_id 
                AND sr.timestamp = lr.max_timestamp
            JOIN pumps p ON sr.device_id = p.pump_id
            WHERE sr.sensor_type = 'water_level'
        ''')
        return [dict(row) for row in cursor.fetchall()]
    



def handle_pump_auth(pump_id, data):
    """Handle pump authentication and registration"""
    try:
        # Add initial validation
        if not pump_id or not pump_id.startswith('PUMP_'):
            print(f"[ERROR] Invalid pump ID: {pump_id}")
            return

        print(f"[PUMP] Processing auth request for: {pump_id}")
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # First check if pump exists
            cursor.execute('SELECT * FROM pumps WHERE pump_id = ?', (pump_id,))
            existing_pump = cursor.fetchone()
            
            if existing_pump:
                print(f"[PUMP] Found existing pump: {pump_id}")
                # Send confirmation for existing pump
                response = {
                    'device_id': pump_id,
                    'status': 'confirmed',
                    'configured': existing_pump['status'] == 'configured'
                }
                mqtt.publish('mynode/pump_auth', json.dumps(response), qos=1)
                
                # Remove from pending if it was there
                if pump_id in pending_pumps:
                    del pending_pumps[pump_id]
                    print(f"[PUMP] Removed {pump_id} from pending pumps")
                
            else:
                # Only add to pending if it's a new request
                if data.get('status') == 'new':
                    print(f"[PUMP] Registering new pump: {pump_id}")
                    
                    # Add to pending pumps
                    pending_pumps[pump_id] = {
                        'timestamp': datetime.now().isoformat(),
                        'status': 'pending'
                    }
                    
                    # Register new pump in database
                    cursor.execute('''
                        INSERT INTO pumps (pump_id, name, status, tank_shape)
                        VALUES (?, ?, ?, ?)
                    ''', (pump_id, pump_id, 'pending', 'none'))
                    conn.commit()
                    
                    # Send registration confirmation
                    response = {
                        'device_id': pump_id,
                        'status': 'registered',
                        'message': 'Ready for setup'
                    }
                    mqtt.publish('mynode/pump_auth', json.dumps(response), qos=1)
                    print(f"[PUMP] Sent registration confirmation to {pump_id}")

    except sqlite3.Error as e:
        print(f"[ERROR] Database error in pump auth: {str(e)}")
        # Cleanup pending pumps entry if database operation fails
        if pump_id in pending_pumps:
            del pending_pumps[pump_id]
    except Exception as e:
        print(f"[ERROR] Error in pump auth: {str(e)}")
        if pump_id in pending_pumps:
            del pending_pumps[pump_id]

@app.route('/api/pump/<pump_id>/setup', methods=['POST'])
def setup_pump(pump_id):
    """Setup pump configuration"""
    if not request.is_json:
        return jsonify({'error': 'Content type must be application/json'}), 400

    try:
        data = request.get_json()
        print(f"Received setup data for pump {pump_id}: {data}")
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        # Validate pump ID
        if not pump_id.startswith('PUMP_'):
            return jsonify({'error': 'Invalid pump ID format'}), 400

        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if pump exists
            cursor.execute('SELECT * FROM pumps WHERE pump_id = ?', (pump_id,))
            pump = cursor.fetchone()
            
            if not pump:
                return jsonify({'error': 'Pump not found'}), 404
                
            try:
                # Validate tank shape
                tank_shape = data.get('tank_shape', 'box')
                if tank_shape not in ['box', 'cylinder']:
                    return jsonify({'error': 'Invalid tank shape'}), 400
                
                # Create update data dictionary
                update_data = {
                    'name': data.get('name'),
                    'location': data.get('location', ''),
                    'tank_shape': tank_shape,
                    'status': 'configured'
                }
                
                # Handle tank dimensions based on shape
                if tank_shape == 'box':
                    try:
                        update_data.update({
                            'tank_length': float(data.get('tank_length', 0)),
                            'tank_width': float(data.get('tank_width', 0)),
                            'tank_height': float(data.get('tank_height', 0)),
                            'tank_diameter': None
                        })
                    except (TypeError, ValueError):
                        return jsonify({'error': 'Invalid box dimensions'}), 400
                else:  # cylinder
                    try:
                        update_data.update({
                            'tank_diameter': float(data.get('tank_diameter', 0)),
                            'tank_height': float(data.get('tank_height', 0)),
                            'tank_length': None,
                            'tank_width': None
                        })
                    except (TypeError, ValueError):
                        return jsonify({'error': 'Invalid cylinder dimensions'}), 400
                
                # Build and execute update query
                set_clause = ', '.join(f'{key} = ?' for key in update_data.keys())
                query = f'UPDATE pumps SET {set_clause} WHERE pump_id = ?'
                params = list(update_data.values()) + [pump_id]
                
                cursor.execute(query, params)
                conn.commit()
                
                # Verify the update
                cursor.execute('SELECT * FROM pumps WHERE pump_id = ?', (pump_id,))
                updated_pump = cursor.fetchone()
                
                if not updated_pump:
                    return jsonify({'error': 'Failed to update pump configuration'}), 500
                
                # Send MQTT confirmation
                mqtt.publish(f'mynode/pump_auth', json.dumps({
                    'device_id': pump_id,
                    'status': 'confirmed',
                    'configured': True
                }), qos=1)
                
                # Return success response
                return jsonify({
                    'status': 'success',
                    'message': 'Pump configuration updated successfully',
                    'pump': dict(updated_pump)
                })
                
            except sqlite3.Error as e:
                print(f"Database error: {str(e)}")
                return jsonify({'error': 'Database error occurred'}), 500
                
    except Exception as e:
        print(f"Setup error for pump {pump_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/pump/<pump_id>/control', methods=['POST'])
def control_pump(pump_id):
    """Send pump control commands"""
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
            
        data = request.get_json()
        command = data.get('command')
        
        if command not in ['on', 'off']:
            return jsonify({'error': 'Invalid command. Must be "on" or "off"'}), 400
        
        # Update pump status in database
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE pumps 
                SET is_running = ?,
                    last_update = CURRENT_TIMESTAMP 
                WHERE pump_id = ?
            ''', (command == 'on', pump_id))
            conn.commit()
        
        # Send control command via MQTT
        control_msg = {
            'device_id': pump_id,
            'command': command,
            'timestamp': datetime.now().isoformat()
        }
        
        # Publish to both topics to ensure compatibility
        mqtt.publish(f'mynode/pump_control', json.dumps(control_msg), qos=1)
        mqtt.publish(f'mynode/{pump_id}/control', json.dumps(control_msg), qos=1)
        
        print(f"[PUMP] Sent control command {command} to {pump_id}")
        
        return jsonify({
            'status': 'success',
            'message': f'Pump {command} command sent successfully'
        })
        
    except Exception as e:
        print(f"[ERROR] Failed to control pump: {str(e)}")
        return jsonify({
            'error': f'Failed to control pump: {str(e)}'
        }), 500

@app.route('/api/pump/<pump_id>/status')
def get_status(pump_id):
    """Get current pump status including water level and volume calculations"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Get pump info directly from pumps table
            cursor.execute('''
                SELECT * FROM pumps WHERE pump_id = ?
            ''', (pump_id,))
            
            pump = cursor.fetchone()
            
            if not pump:
                return jsonify({'error': 'Pump not found'}), 404
            
            # Convert to dictionary for easier manipulation
            pump_dict = dict(pump)
            
            # Build status response
            status = {
                'pump_id': pump_dict['pump_id'],
                'name': pump_dict['name'],
                'location': pump_dict['location'],
                'status': pump_dict['status'],
                'is_running': bool(pump_dict['is_running']),
                'last_update': pump_dict['last_update']
            }
            
            # Calculate volume using last_reading from pumps table
            if pump_dict['last_reading'] is not None:
                try:
                    tank_volume = None
                    water_level = float(pump_dict['last_reading'])
                    max_height = float(pump_dict['tank_height'] or 100)
                    
                    # Calculate volume based on tank shape
                    if pump_dict['tank_shape'] == 'box':
                        if pump_dict['tank_length'] and pump_dict['tank_width']:
                            volume = (float(pump_dict['tank_length']) * 
                                    float(pump_dict['tank_width']) * 
                                    (max_height - water_level)) / 1000  # Convert to liters
                            
                            tank_volume = {
                                'volume': round(volume, 2),
                                'percentage': round(((max_height - water_level) / max_height) * 100, 1),
                                'tank_shape': 'box',
                                'water_level': round(water_level, 1),
                                'max_height': round(max_height, 1)
                            }
                            
                    elif pump_dict['tank_shape'] == 'cylinder':
                        if pump_dict['tank_diameter']:
                            radius = float(pump_dict['tank_diameter']) / 2
                            volume = (3.14159 * radius * radius * 
                                    (max_height - water_level)) / 1000  # Convert to liters
                            
                            tank_volume = {
                                'volume': round(volume, 2),
                                'percentage': round(((max_height - water_level) / max_height) * 100, 1),
                                'tank_shape': 'cylinder',
                                'water_level': round(water_level, 1),
                                'max_height': round(max_height, 1)
                            }
                    
                    if tank_volume:
                        # Ensure percentage is between 0 and 100
                        tank_volume['percentage'] = max(0, min(100, tank_volume['percentage']))
                        status['volume'] = tank_volume
                        
                except (TypeError, ValueError) as e:
                    logging.error(f"Error calculating volume for pump {pump_id}: {str(e)}")
            
            return jsonify(status)
            
    except Exception as e:
        logging.error(f"[ERROR] Failed to get pump status: {str(e)}")
        return jsonify({
            'error': f'Error getting pump status: {str(e)}'
        }), 500



# Add this after initializing other components (like mqtt, socketio, etc.)
class PumpScheduler:
    def __init__(self):
        self.scheduler_thread = None
        self.running = False
        self.jobs = {}
        self.load_existing_schedules()
    
    def load_existing_schedules(self):
        """Load existing schedules from database on startup"""
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                # Only load future schedules and today's remaining schedules
                cursor.execute('''
                    SELECT pump_id, schedule_time, duration 
                    FROM schedules 
                    WHERE schedule_date > DATE('now')
                    OR (schedule_date = DATE('now') AND schedule_time > TIME('now'))
                ''')
                schedules = cursor.fetchall()
                
                for schedule in schedules:
                    self.add_job(
                        pump_id=schedule['pump_id'],
                        schedule_time=schedule['schedule_time'],
                        duration=schedule['duration']
                    )
                print(f"[SCHEDULER] Loaded {len(schedules)} existing schedules")
                
                # Clean up any old schedules
                cursor.execute('''
                    DELETE FROM schedules 
                    WHERE schedule_date < DATE('now')
                    OR (schedule_date = DATE('now') AND schedule_time <= TIME('now'))
                ''')
                conn.commit()
                
        except Exception as e:
            print(f"[SCHEDULER ERROR] Failed to load existing schedules: {str(e)}")
    
    def start(self):
        """Start the scheduler thread"""
        if not self.running:
            self.running = True
            self.scheduler_thread = threading.Thread(target=self._run_scheduler)
            self.scheduler_thread.daemon = True
            self.scheduler_thread.start()
            print("[SCHEDULER] Scheduler thread started")
    
    def _run_scheduler(self):
        """Run the scheduler loop with proper error handling"""
        print("[SCHEDULER] Scheduler loop starting")
        while self.running:
            try:
                schedule.run_pending()
                time_module.sleep(1)
            except Exception as e:
                print(f"[SCHEDULER ERROR] Error in scheduler loop: {str(e)}")
                time_module.sleep(5)  # Wait before retrying
    
    def add_job(self, pump_id, schedule_time, duration):
        """Add a new scheduled job with logging"""
        try:
            job_id = f"{pump_id}_{schedule_time}"
            print(f"[SCHEDULER] Adding job {job_id} for pump {pump_id}")
            
            # Cancel existing job if any
            if job_id in self.jobs:
                print(f"[SCHEDULER] Cancelling existing job {job_id}")
                schedule.cancel_job(self.jobs[job_id])
            
            # Create new job with explicit function call
            job = schedule.every().day.at(schedule_time).do(
                handle_scheduled_pump,
                pump_id=pump_id,
                duration=duration
            )
            
            self.jobs[job_id] = job
            print(f"[SCHEDULER] Successfully added job {job_id}")
            return True
            
        except Exception as e:
            print(f"[SCHEDULER ERROR] Failed to add job {pump_id}_{schedule_time}: {str(e)}")
            return False

def handle_scheduled_pump(pump_id, duration):
    """Handle scheduled pump operation with cleanup after completion"""
    print(f"[SCHEDULE] Starting scheduled pump operation for {pump_id}, duration: {duration} minutes")
    
    try:
        # Get the current schedule time before we delete it
        current_time = datetime.now().strftime("%H:%M")
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # First remove the completed schedule from database
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Delete the schedule that's being executed
            cursor.execute('''
                DELETE FROM schedules 
                WHERE pump_id = ? 
                AND schedule_date = ? 
                AND schedule_time = ?
            ''', (pump_id, current_date, current_time))
            
            # Update pump status
            cursor.execute('''
                UPDATE pumps 
                SET is_running = TRUE,
                    last_update = CURRENT_TIMESTAMP 
                WHERE pump_id = ?
            ''', (pump_id,))
            
            conn.commit()
            print(f"[SCHEDULE] Removed completed schedule for pump {pump_id}")
        
        # Send MQTT command to turn on pump
        control_msg = {
            'device_id': pump_id,
            'command': 'on',
            'duration': duration,
            'scheduled': True,
            'timestamp': datetime.now().isoformat()
        }
        
        mqtt.publish(f'mynode/pump_control', json.dumps(control_msg), qos=1)
        mqtt.publish(f'mynode/{pump_id}/control', json.dumps(control_msg), qos=1)
        print(f"[SCHEDULE] Sent ON command via MQTT for pump {pump_id}")
        
        # Schedule turn off after duration
        def turn_off_pump():
            try:
                print(f"[SCHEDULE] Initiating scheduled turn off for pump {pump_id}")
                
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        UPDATE pumps 
                        SET is_running = FALSE,
                            last_update = CURRENT_TIMESTAMP 
                        WHERE pump_id = ?
                    ''', (pump_id,))
                    conn.commit()
                
                off_msg = {
                    'device_id': pump_id,
                    'command': 'off',
                    'scheduled': True,
                    'timestamp': datetime.now().isoformat()
                }
                
                mqtt.publish(f'mynode/pump_control', json.dumps(off_msg), qos=1)
                mqtt.publish(f'mynode/{pump_id}/control', json.dumps(off_msg), qos=1)
                
                print(f"[SCHEDULE] Successfully turned off pump {pump_id}")
                
                # Remove the schedule from the scheduler's job list
                job_id = f"{pump_id}_{current_time}"
                if job_id in pump_scheduler.jobs:
                    schedule.cancel_job(pump_scheduler.jobs[job_id])
                    del pump_scheduler.jobs[job_id]
                    print(f"[SCHEDULE] Cleaned up completed schedule job {job_id}")
                
            except Exception as e:
                print(f"[SCHEDULE ERROR] Failed to turn off pump {pump_id}: {str(e)}")
        
        # Set up the turn-off timer
        timer = threading.Timer(duration * 60, turn_off_pump)
        timer.daemon = True
        timer.start()
        
        print(f"[SCHEDULE] Successfully initiated pump {pump_id} operation")
        return True
        
    except Exception as e:
        print(f"[SCHEDULE ERROR] Failed to handle scheduled pump {pump_id}: {str(e)}")
        return False

@app.route('/api/pump/<pump_id>/schedule', methods=['POST'])
def add_schedule(pump_id):
    """Add a new pump schedule"""
    try:
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
            
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        # Validate required fields
        required_fields = ['date', 'time', 'duration']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400
            
        try:
            schedule_datetime = datetime.strptime(f"{data['date']} {data['time']}", '%Y-%m-%d %H:%M')
            if schedule_datetime < datetime.now():
                return jsonify({'error': 'Cannot schedule in the past'}), 400
        except ValueError as e:
            return jsonify({'error': f'Invalid date or time format: {str(e)}'}), 400
            
        try:
            duration = int(data['duration'])
            if not 1 <= duration <= 120:
                return jsonify({'error': 'Duration must be between 1 and 120 minutes'}), 400
        except (TypeError, ValueError) as e:
            return jsonify({'error': f'Invalid duration: {str(e)}'}), 400
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Check if pump exists
            cursor.execute('SELECT 1 FROM pumps WHERE pump_id = ?', (pump_id,))
            if not cursor.fetchone():
                return jsonify({'error': 'Pump not found'}), 404
            
            # Check for conflicting schedules
            cursor.execute('''
                SELECT 1 FROM schedules 
                WHERE pump_id = ? 
                AND schedule_date = ? 
                AND schedule_time = ?
            ''', (pump_id, data['date'], data['time']))
            
            if cursor.fetchone():
                return jsonify({'error': 'A schedule already exists for this time'}), 409
            
            # Add to database
            cursor.execute('''
                INSERT INTO schedules (pump_id, schedule_date, schedule_time, duration)
                VALUES (?, ?, ?, ?)
            ''', (pump_id, data['date'], data['time'], duration))
            
            # Add to scheduler - just pass the required arguments
            success = pump_scheduler.add_job(
                pump_id=pump_id, 
                schedule_time=data['time'], 
                duration=duration
            )
            
            if not success:
                conn.rollback()
                return jsonify({'error': 'Failed to add schedule to scheduler'}), 500
            
            conn.commit()
            
            return jsonify({
                'status': 'success',
                'message': 'Schedule added successfully'
            })
            
    except Exception as e:
        logging.error(f"Error in add_schedule: {str(e)}")
        return jsonify({'error': f'Failed to add schedule: {str(e)}'}), 500
    

@app.route('/api/pump/<pump_id>/schedules', methods=['GET'])
def get_schedules(pump_id):
    """Get all schedules for a pump"""
    try:
        print("Getting schedules for pump:", pump_id)
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM schedules 
                WHERE pump_id = ? 
                AND (schedule_date > DATE('now') 
                     OR (schedule_date = DATE('now') 
                         AND schedule_time > TIME('now')))
                ORDER BY schedule_date, schedule_time
            ''', (pump_id,))
            
            schedules = [dict(row) for row in cursor.fetchall()]
            print("Found schedules:", schedules)
            
            response = jsonify(schedules)
            print("Sending response:", response.get_data(as_text=True))
            return response
            
    except Exception as e:
        print("Error in get_schedules:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({
            'error': f'Failed to fetch schedules: {str(e)}'
        }), 500



@app.route('/api/pump/<pump_id>/schedule', methods=['DELETE'])
def delete_schedule(pump_id):
    """Delete a pump schedule with improved error handling"""
    try:
        print(f"[DELETE] Attempting to delete schedule for pump {pump_id}")
        
        if not request.is_json:
            print("[DELETE] Error: Request is not JSON")
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        
        data = request.get_json()
        print(f"[DELETE] Received data: {data}")
        
        if not data or 'date' not in data or 'time' not in data:
            print("[DELETE] Error: Missing required fields")
            return jsonify({'error': 'Missing required fields (date and time)'}), 400
        
        # Log the values we're using for deletion
        print(f"[DELETE] Using pump_id: {pump_id}, date: {data['date']}, time: {data['time']}")
        
        with get_db() as conn:
            cursor = conn.cursor()
            
            # First check if the schedule exists
            cursor.execute('''
                SELECT 1 FROM schedules 
                WHERE pump_id = ? 
                AND schedule_date = ?
                AND schedule_time = ?
            ''', (pump_id, data['date'], data['time']))
            
            if not cursor.fetchone():
                print(f"[DELETE] Schedule not found for pump {pump_id}")
                return jsonify({'error': 'Schedule not found'}), 404
            
            # Delete from database
            cursor.execute('''
                DELETE FROM schedules 
                WHERE pump_id = ? 
                AND schedule_date = ?
                AND schedule_time = ?
            ''', (pump_id, data['date'], data['time']))
            
            # Remove from scheduler
            job_id = f"{pump_id}_{data['time']}"
            if hasattr(pump_scheduler, 'jobs') and job_id in pump_scheduler.jobs:
                try:
                    schedule.cancel_job(pump_scheduler.jobs[job_id])
                    del pump_scheduler.jobs[job_id]
                    print(f"[DELETE] Removed job {job_id} from scheduler")
                except Exception as e:
                    print(f"[DELETE] Warning: Error removing job from scheduler: {str(e)}")
            
            conn.commit()
            print(f"[DELETE] Successfully deleted schedule for pump {pump_id}")
            
            return jsonify({
                'status': 'success',
                'message': 'Schedule deleted successfully'
            })
            
    except Exception as e:
        print(f"[DELETE] Error deleting schedule: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



@mqtt.on_message()
def handle_mqtt_message(client, userdata, message):
    """
    Handle incoming MQTT messages for both sensors and pumps.
    """
    try:
        print(f"\n[MQTT] Received message on topic: {message.topic}")
        
        # Parse payload
        try:
            payload = message.payload.decode()
            print(f"[MQTT] Raw payload: {payload}")
            data = json.loads(payload)
        except json.JSONDecodeError:
            print(f"[ERROR] Invalid JSON payload: {payload}")
            return
        except Exception as e:
            print(f"[ERROR] Error decoding message: {str(e)}")
            return

        # Get device ID from payload
        device_id = data.get('device_id')
        if not device_id:
            print("[ERROR] No device_id in message")
            return

        # Handle messages based on topic
        topic = message.topic.lower()  # Normalize topic case

        # Handle sensor auth
        if topic == 'mynode/auth':
            if data.get('action') == 'auth_request':
                handle_sensor_request(device_id, data)
            return

        # Handle pump auth
        if topic == 'mynode/pump_auth':
            if device_id.startswith('PUMP_'):
                handle_pump_auth(device_id, data)
            return

        # Handle sensor readings
        if topic in ['mynode/temperature', 'mynode/moisture']:
            handle_sensor_data(topic, device_id, data)
            return

        # Handle pump readings/status
        if topic == 'mynode/water_level':
            if device_id.startswith('PUMP_'):
                handle_water_level(device_id, data)
            return

        if topic == 'mynode/pump_status':
            if device_id.startswith('PUMP_'):
                handle_pump_status(device_id, data)
            return

        # Handle sleep config
        if topic == 'mynode/default/config/sleep':
            handle_sleep_config(device_id, data.get('action'), data)
            return

    except Exception as e:
        print(f"[ERROR] Error processing message: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    init_db()
    pump_scheduler = PumpScheduler()
    pump_scheduler.start()
    socketio.run(app, host='0.0.0.0', port=5000, use_reloader=False, debug=True )
    
