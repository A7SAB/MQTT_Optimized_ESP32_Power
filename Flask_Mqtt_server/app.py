import eventlet
import json
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_mqtt import Mqtt
from flask_socketio import SocketIO
from flask_bootstrap import Bootstrap
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os
from datetime import datetime
import sqlite3
import threading

eventlet.monkey_patch()

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

class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

# Database Functions
def get_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    return db

class SensorData:
    def __init__(self):
        self.sensors = []
        self.readings = []
        self.settings = {
            'alert_threshold_temp': 30,
            'alert_threshold_humid': 80,
            'notification_email': 'admin@example.com',
            'data_retention_days': 30
        }
        

def get_device_sleep_time(device_id):
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            # Debug: Print the device_id we're searching for
            print(f"[DEBUG] Searching sleep time for device: {device_id}")
            
            cursor.execute('''
                SELECT sleep_duration 
                FROM device_settings 
                WHERE device_id = ? 
                ORDER BY last_updated DESC 
                LIMIT 1
            ''', (device_id,))
            
            result = cursor.fetchone()
            
            # Debug: Print what we found in the database
            if result:
                print(f"[DEBUG] Found sleep time in database: {result[0]} seconds")
                return result[0]
            else:
                print(f"[DEBUG] No sleep time found for device {device_id}, using default")
                return 30
    except Exception as e:
        print(f"[ERROR] Database error while getting sleep time: {str(e)}")
        return 30


def init_db():
    with app.app_context():
        with get_db() as conn:
            cursor = conn.cursor()
            
            # Drop existing tables to ensure correct schema
            print("├── Dropping existing tables...")
            cursor.execute('DROP TABLE IF EXISTS sensor_readings')
            cursor.execute('DROP TABLE IF EXISTS device_settings')
            cursor.execute('DROP TABLE IF EXISTS settings')  # Add this line
            
            # Create users table
            print("├── Creating users table...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL
                )
            ''')
            
            # Create new sensor_readings table with device_id
            print("├── Creating sensor_readings table...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sensor_readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    sensor_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create device settings table with last_seen
            print("├── Creating device_settings table...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT UNIQUE NOT NULL,
                    sleep_duration INTEGER NOT NULL DEFAULT 30,
                    last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Create settings table
            print("├── Creating settings table...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_threshold_temp REAL DEFAULT 30,
                    alert_threshold_humid REAL DEFAULT 80,
                    notification_email TEXT DEFAULT 'admin@example.com',
                    data_retention_days INTEGER DEFAULT 30
                )
            ''')
            
            # Insert default settings if not exists
            cursor.execute('SELECT COUNT(*) FROM settings')
            if cursor.fetchone()[0] == 0:
                cursor.execute('''
                    INSERT INTO settings (
                        alert_threshold_temp,
                        alert_threshold_humid,
                        notification_email,
                        data_retention_days
                    ) VALUES (30, 80, 'admin@example.com', 30)
                ''')
            
            # Add indexes for faster queries
            print("├── Creating indexes...")
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_sensor_readings_device 
                ON sensor_readings(device_id, timestamp)
            ''')
            
            # Add default admin user if not exists
            print("├── Setting up admin user...")
            cursor.execute('SELECT * FROM users WHERE username = ?', ('admin',))
            if not cursor.fetchone():
                default_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
                password_hash = generate_password_hash(default_password)
                cursor.execute(
                    'INSERT INTO users (username, password_hash) VALUES (?, ?)',
                    ('admin', password_hash)
                )
            
            conn.commit()
            print("└── Database initialization complete")

            # Verify tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [table[0] for table in cursor.fetchall()]
            print(f"    Created tables: {', '.join(tables)}")

def get_all_sensors():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 
                d.device_id,
                d.sleep_duration as sleep_time,
                t.value as temperature,
                h.value as humidity,
                COALESCE(d.last_seen, t.timestamp, h.timestamp) as last_update
            FROM device_settings d
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
                WHERE sensor_type = 'humidity'
                AND timestamp IN (
                    SELECT MAX(timestamp)
                    FROM sensor_readings
                    WHERE sensor_type = 'humidity'
                    GROUP BY device_id
                )
            ) h ON d.device_id = h.device_id
            ORDER BY d.last_seen DESC
        ''')
        return cursor.fetchall()

@mqtt.on_message()
def get_sensor_readings(sensor_id=None, limit=10):
    with get_db() as conn:
        cursor = conn.cursor()
        if sensor_id and sensor_id != 'all':
            cursor.execute('''
                SELECT device_id, sensor_type, value, timestamp
                FROM sensor_readings
                WHERE device_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (sensor_id, limit))
        else:
            cursor.execute('''
                SELECT device_id, sensor_type, value, timestamp
                FROM sensor_readings
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))
        return cursor.fetchall()

def get_latest_values():
    with get_db() as conn:
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

def insert_reading(sensor_type, value):
    with db_lock:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO sensor_readings (sensor_type, value) VALUES (?, ?)',
                (sensor_type, value)
            )
            conn.commit()

def get_user_by_username(username):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
        user_data = cursor.fetchone()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password_hash'])
    return None

def get_user_by_id(user_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            return User(user_data['id'], user_data['username'], user_data['password_hash'])
    return None

@login_manager.user_loader
def load_user(user_id):
    return get_user_by_id(int(user_id))

# Routes
@app.route('/create_user', methods=['GET', 'POST'])
@login_required
def create_user():
    if current_user.username != 'admin':
        flash('Only admin can create new users')
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if not username or not password:
            flash('Username and password are required')
            return redirect(url_for('create_user'))
            
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                password_hash = generate_password_hash(password)
                cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                             (username, password_hash))
                conn.commit()
                flash(f'User {username} created successfully')
                return redirect(url_for('dashboard'))
        except sqlite3.IntegrityError:
            flash('Username already exists')
        except Exception as e:
            flash(f'Error creating user: {str(e)}')
            
    return render_template('create_user.html')

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

sensor_data = SensorData()

@app.route('/dashboard')
@login_required
def dashboard():
    try:
        sensors = get_all_sensors()
        latest_readings = get_sensor_readings(limit=10)
        return render_template('dashboard.html', 
                             sensors=sensors,
                             latest_readings=latest_readings,
                             active_page='dashboard')  # Add this for nav highlighting
    except Exception as e:
        print(f"Dashboard error: {str(e)}")
        flash('Error loading dashboard data')
        return render_template('dashboard.html', sensors=[], latest_readings=[])
@app.route('/analytics')
@login_required
def analytics():
    # Get readings from the database
    with get_db() as conn:
        cursor = conn.cursor()
        # Get last 24 hours readings
        cursor.execute('''
            SELECT * FROM sensor_readings 
            WHERE timestamp >= datetime('now', '-1 day')
            ORDER BY timestamp DESC
        ''')
        readings_24h = cursor.fetchall()

    # Calculate analytics
    temp_readings = [r['value'] for r in readings_24h if r['sensor_type'] == 'temperature']
    humid_readings = [r['value'] for r in readings_24h if r['sensor_type'] == 'humidity']
    
    analytics_data = {
        'total_sensors': len(get_all_sensors()),
        'readings_24h': len(readings_24h),
        'temp_range': {
            'min': min(temp_readings) if temp_readings else 0,
            'max': max(temp_readings) if temp_readings else 0
        },
        'humid_range': {
            'min': min(humid_readings) if humid_readings else 0,
            'max': max(humid_readings) if humid_readings else 0
        }
    }
    
    return render_template('analytics.html', 
                         analytics=analytics_data, 
                         readings=readings_24h)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE settings SET 
                    alert_threshold_temp = ?,
                    alert_threshold_humid = ?,
                    notification_email = ?,
                    data_retention_days = ?
                ''', (
                    float(request.form['alert_threshold_temp']),
                    float(request.form['alert_threshold_humid']),
                    request.form['notification_email'],
                    int(request.form['data_retention_days'])
                ))
                conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})
    
    # Get settings from database
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM settings LIMIT 1')
        settings = cursor.fetchone()
    
    return render_template('settings.html', settings=settings)

@app.route('/history')
@login_required
def history():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get total count
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM sensor_readings')
        total_readings = cursor.fetchone()[0]
        
        # Get paginated readings
        cursor.execute('''
            SELECT * FROM sensor_readings 
            ORDER BY timestamp DESC 
            LIMIT ? OFFSET ?
        ''', (per_page, (page-1)*per_page))
        readings = cursor.fetchall()
    
    total_pages = (total_readings + per_page - 1) // per_page
    
    return render_template('history.html', 
                         readings=readings,
                         page=page,
                         total_pages=total_pages)

@app.route('/api/reset-database', methods=['POST'])
@login_required
def reset_database():
    try:
        with db_lock:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM sensor_readings')
                conn.commit()
        return jsonify({'success': True, 'message': 'Database reset successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# Add API endpoint to update sleep time
@app.route('/api/set-sleep-time', methods=['POST'])
@login_required
def set_sleep_time():
    try:
        data = request.get_json()
        print(f"[DEBUG] Received data: {data}")  # Debug log
        
        if not data:
            return jsonify({'success': False, 'message': 'No data received'}), 400
            
        sleep_time = data.get('sleep_time')
        device_id = data.get('device_id')
        
        if not device_id:
            return jsonify({'success': False, 'message': 'No device ID provided'}), 400
            
        if not isinstance(sleep_time, (int, float)) or sleep_time < 1:
            return jsonify({'success': False, 'message': 'Invalid sleep time value'}), 400

        print(f"[DEBUG] Updating sleep time: Device={device_id}, Time={sleep_time}")
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE device_settings 
                SET sleep_duration = ?, 
                    last_seen = CURRENT_TIMESTAMP 
                WHERE device_id = ?
            ''', (sleep_time, device_id))
            
            if cursor.rowcount == 0:
                cursor.execute('''
                    INSERT INTO device_settings (device_id, sleep_duration)
                    VALUES (?, ?)
                ''', (device_id, sleep_time))
            
            conn.commit()

        # Send MQTT update
        response = {
            'device_id': device_id,
            'sleep_time': sleep_time
        }
        mqtt.publish(f'mynode/{device_id}/config/sleep', json.dumps(response))
        print(f"[MQTT] Published sleep time update: {response}")
        
        return jsonify({
            'success': True,
            'message': f'Sleep time updated to {sleep_time} seconds'
        })

    except Exception as e:
        print(f"[ERROR] Failed to set sleep time: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Failed to update sleep time: {str(e)}'
        }), 400
        
# MQTT Handler
@mqtt.on_connect()
def handle_connect(client, userdata, flags, rc):
    mqtt.subscribe('mynode/Temperature')
    mqtt.subscribe('mynode/Humidity')
    print(f"Connected to MQTT Broker with result code: {rc}")

# MQTT Handler
@mqtt.on_connect()
def handle_connect(client, userdata, flags, rc):
    # Subscribe to all relevant topics
    mqtt.subscribe('mynode/Temperature')
    mqtt.subscribe('mynode/Humidity')
    mqtt.subscribe('mynode/auth')
    mqtt.subscribe('mynode/default/config/sleep')
    print(f"Connected to MQTT Broker with result code: {rc}")
    print("Subscribed to topics: Temperature, Humidity, Auth, Sleep")

@app.route('/api/get-readings')
@login_required
def get_readings():
    sensor_id = request.args.get('sensor', 'all')
    readings = get_sensor_readings(sensor_id)
    return jsonify({'success': True, 'readings': readings})

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
            conn.commit()
            
        return jsonify({'success': True, 'message': 'Sensor deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@mqtt.on_message()
def handle_mqtt_message(client, userdata, message):
    try:
        print(f"\n[MQTT] Received message on topic: {message.topic}")
        payload = message.payload.decode()
        print(f"[MQTT] Raw payload: {payload}")

        data = json.loads(payload)
        device_id = data.get('device_id')
        if not device_id:
            print("[ERROR] No device_id in message")
            return

        topic = message.topic
        action = data.get('action')
        print(f"[MQTT] Processing message from {device_id} on topic: {topic}")

        # Handle authentication request
        if topic == 'mynode/auth':
            if action == 'auth_request':
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT OR IGNORE INTO device_settings 
                        (device_id, sleep_duration, last_seen) 
                        VALUES (?, 30, CURRENT_TIMESTAMP)
                    ''', (device_id,))
                    conn.commit()

                response = {
                    'device_id': device_id,
                    'status': 'approved'
                }
                mqtt.publish('mynode/auth', json.dumps(response))
                print(f"[AUTH] Sent approval to device: {device_id}")

        # Handle sleep time request
        elif topic == 'mynode/default/config/sleep':
            if action == 'get_sleep_time':
                with get_db() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        'SELECT sleep_duration FROM device_settings WHERE device_id = ?',
                        (device_id,)
                    )
                    result = cursor.fetchone()
                    sleep_time = result[0] if result else 30

                    # Update last_seen
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

        # Handle sensor data
        elif topic in ['mynode/Temperature', 'mynode/Humidity']:
            sensor_type = topic.split('/')[-1].lower()
            value = data.get(sensor_type.lower())
            
            if value is not None:
                with get_db() as conn:
                    cursor = conn.cursor()
                    # Insert new reading
                    cursor.execute('''
                        INSERT INTO sensor_readings (device_id, sensor_type, value)
                        VALUES (?, ?, ?)
                    ''', (device_id, sensor_type, float(value)))
                    
                    # Update last_seen
                    cursor.execute('''
                        UPDATE device_settings 
                        SET last_seen = CURRENT_TIMESTAMP 
                        WHERE device_id = ?
                    ''', (device_id,))
                    
                    conn.commit()

                # Send acknowledgment
                response = {
                    'device_id': device_id,
                    'status': 'received'
                }
                mqtt.publish('mynode/ack', json.dumps(response))
                print(f"[DATA] Acknowledged {sensor_type} reading from {device_id}")

                # Emit to websocket clients
                socketio.emit('mqtt_message', {
                    'device_id': device_id,
                    'topic': topic,
                    'value': value,
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })

    except Exception as e:
        print(f"[ERROR] Message processing error: {str(e)}")
        import traceback
        print(traceback.format_exc())

def create_app():
    # Initialize Flask-MQTT
    mqtt.init_app(app)
    
    # Initialize database
    init_db()
    
    return app

if __name__ == '__main__':
    app = create_app()
    print(f"\n[{datetime.now()}] Starting Flask application...")
    print(f"├── Current working directory: {os.getcwd()}")
    print(f"├── Initializing database...")
    
    try:
        init_db()
        print(f"├── Database initialized successfully")
        print(f"├── MQTT Broker: {app.config['MQTT_BROKER_URL']}:{app.config['MQTT_BROKER_PORT']}")
        print(f"└── Server running on http://0.0.0.0:5000")
        
        socketio.run(app, 
                    host='0.0.0.0', 
                    port=5000, 
                    use_reloader=False, 
                    debug=True,
                    allow_unsafe_werkzeug=True)
    except Exception as e:
        print(f"[ERROR] Failed to start server: {str(e)}")
        import traceback
        print(traceback.format_exc())