import paho.mqtt.client as mqtt
import time
import json
import random
import logging
from datetime import datetime
from enum import Enum

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# State Machine States
class State(Enum):
    STATE_INIT = "INIT"
    STATE_AUTH = "AUTH"
    STATE_GET_SLEEP = "GET_SLEEP"
    STATE_PUBLISH = "PUBLISH"
    STATE_WAIT_ACK = "WAIT_ACK"
    STATE_SLEEP = "SLEEP"

class ESP32FakeSensor:
    def __init__(self, device_id=None):
        # Device configuration
        self.device_id = device_id or f"Sensor32_{random.randint(10000, 99999)}"
        self.sleep_duration = 30  # Default sleep duration in seconds
        
        # MQTT settings
        self.mqtt_server = "broker.hivemq.com"
        self.mqtt_port = 1883
        
        # MQTT topics
        self.TOPIC_TEMP = "mynode/Temperature"
        self.TOPIC_MOISTURE = "mynode/moisture"
        self.TOPIC_AUTH = "mynode/auth"
        self.TOPIC_ACK = "mynode/ack"
        self.TOPIC_SLEEP = "mynode/default/config/sleep"
        
        # State variables
        self.current_state = State.STATE_INIT
        self.is_authenticated = False
        self.data_acknowledged = False
        self.data_sent = False
        self.sleep_time_received = False
        
        # Timing variables
        self.start_time = time.time()
        self.last_message_time = 0
        
        # Initialize MQTT client
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect
        
        self.setup()

    def setup(self):
        """Initial setup - similar to ESP32 setup()"""
        logging.info(f"[INIT] Starting Fake ESP32 Sensor Node: {self.device_id}")
        self.start_time = time.time()
        self.set_state(State.STATE_AUTH)

    def set_state(self, new_state):
        """Change state and update timing"""
        self.current_state = new_state
        self.last_message_time = time.time()
        logging.info(f"[STATE] Changed to state: {new_state.value}")

    def connect(self):
        """Connect to MQTT broker"""
        try:
            logging.info(f"[MQTT] Connecting to broker: {self.mqtt_server}")
            self.client.connect(self.mqtt_server, self.mqtt_port, 60)
            self.client.loop_start()
        except Exception as e:
            logging.error(f"[ERROR] Connection failed: {str(e)}")
            self.sleep()

    def on_connect(self, client, userdata, flags, rc):
        """Handle MQTT connection"""
        if rc == 0:
            logging.info("[MQTT] Connected successfully")
            # Subscribe to topics
            self.client.subscribe(self.TOPIC_AUTH)
            self.client.subscribe(self.TOPIC_ACK)
            self.client.subscribe(f"{self.TOPIC_SLEEP}")
        else:
            logging.error(f"[MQTT] Connection failed with code {rc}")

    def on_disconnect(self, client, userdata, rc):
        """Handle MQTT disconnection"""
        logging.warning("[MQTT] Disconnected from broker")
        if rc != 0:
            self.sleep()

    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages"""
        try:
            payload = json.loads(msg.payload.decode())
            logging.debug(f"[MQTT] Received on {msg.topic}: {payload}")
            
            # Handle authentication response
            if msg.topic == self.TOPIC_AUTH:
                if payload.get('status') == 'approved':
                    logging.info("[AUTH] Device authenticated!")
                    self.is_authenticated = True
                    self.set_state(State.STATE_GET_SLEEP)
                    
            # Handle sleep time response
            elif msg.topic == self.TOPIC_SLEEP:
                if payload.get('device_id') == self.device_id and 'sleep_time' in payload:
                    self.sleep_duration = int(payload['sleep_time'])
                    logging.info(f"[SLEEP] Updated sleep time: {self.sleep_duration} seconds")
                    self.sleep_time_received = True
                    self.set_state(State.STATE_PUBLISH)
                    
            # Handle data acknowledgment
            elif msg.topic == self.TOPIC_ACK:
                if payload.get('status') == 'received':
                    logging.info("[ACK] Data acknowledged by server")
                    self.data_acknowledged = True
                    self.set_state(State.STATE_SLEEP)
                    
        except json.JSONDecodeError:
            logging.error("[ERROR] Failed to parse message payload")
        except Exception as e:
            logging.error(f"[ERROR] Message handling error: {str(e)}")

    def request_authentication(self):
        """Send authentication request"""
        auth_request = {
            'device_id': self.device_id,
            'action': 'auth_request'
        }
        self.client.publish(self.TOPIC_AUTH, json.dumps(auth_request))
        logging.info("[AUTH] Authentication request sent")
        self.last_message_time = time.time()

    def request_sleep_time(self):
        """Request sleep time configuration"""
        sleep_request = {
            'device_id': self.device_id,
            'action': 'get_sleep_time'
        }
        self.client.publish(self.TOPIC_SLEEP, json.dumps(sleep_request))
        logging.info("[SLEEP] Sleep time request sent")
        self.last_message_time = time.time()

    def generate_sensor_data(self):
        """Generate fake sensor readings"""
        temperature = round(random.uniform(20, 30), 1)
        humidity = round(random.uniform(30, 70), 1)
        return temperature, humidity

    def publish_sensor_data(self):
        """Publish sensor readings"""
        if self.data_sent:
            return

        temperature, humidity = self.generate_sensor_data()
        
        # Prepare data payload
        data = {
            'device_id': self.device_id,
            'temperature': temperature,
            'moisture': humidity,
            'timestamp': datetime.now().isoformat()
        }
        
        json_data = json.dumps(data)
        
        # Publish to both topics
        if (self.client.publish(self.TOPIC_TEMP, json_data).rc == 0 and
            self.client.publish(self.TOPIC_MOISTURE, json_data).rc == 0):
            logging.info(f"[DATA] Published - T: {temperature}°C, H: {humidity}%")
            self.data_sent = True
            self.set_state(State.STATE_WAIT_ACK)
        else:
            logging.error("[ERROR] Failed to publish data")

    def sleep(self):
        """Simulate ESP32 deep sleep"""
        logging.info(f"[SLEEP] Going to sleep for {self.sleep_duration} seconds")
        self.client.loop_stop()
        self.client.disconnect()
        time.sleep(self.sleep_duration)
        
        # Reset states for next cycle
        self.is_authenticated = False
        self.data_acknowledged = False
        self.data_sent = False
        self.sleep_time_received = False
        self.current_state = State.STATE_INIT
        
        # Reconnect
        self.connect()
        self.set_state(State.STATE_AUTH)

    def run(self):
        """Main run loop - implements state machine"""
        self.connect()
        
        while True:
            try:
                # Check for timeout (2 minutes total runtime)
                if time.time() - self.start_time > 120:
                    logging.warning("[TIMEOUT] Total runtime exceeded")
                    self.sleep()
                    continue

                # State machine
                if self.current_state == State.STATE_AUTH:
                    if not self.is_authenticated and time.time() - self.last_message_time > 5:
                        self.request_authentication()

                elif self.current_state == State.STATE_GET_SLEEP:
                    if not self.sleep_time_received and time.time() - self.last_message_time > 5:
                        self.request_sleep_time()
                    if not self.sleep_time_received and time.time() - self.last_message_time > 30:
                        logging.warning("[SLEEP] No sleep time received, using default")
                        self.set_state(State.STATE_PUBLISH)

                elif self.current_state == State.STATE_PUBLISH:
                    if not self.data_sent:
                        self.publish_sensor_data()

                elif self.current_state == State.STATE_WAIT_ACK:
                    if not self.data_acknowledged and time.time() - self.last_message_time > 10:
                        logging.warning("[TIMEOUT] No acknowledgment received")
                        self.sleep()

                elif self.current_state == State.STATE_SLEEP:
                    self.sleep()

                time.sleep(0.1)  # Prevent CPU overuse

            except Exception as e:
                logging.error(f"[ERROR] Runtime error: {str(e)}")
                time.sleep(5)

def main():
    # Create and run multiple fake sensors
    sensors = [
        ESP32FakeSensor("Sensor32_A1B2C3"),
        ESP32FakeSensor("Sensor32_D4E5F6"),
        ESP32FakeSensor("Sensor32_G7H8I9")
    ]
    
    # Start each sensor in a separate thread
    import threading
    threads = []
    for sensor in sensors:
        thread = threading.Thread(target=sensor.run)
        thread.daemon = True
        thread.start()
        threads.append(thread)
        time.sleep(2)  # Stagger the starts
    
    # Wait for all threads
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down sensors...")

if __name__ == "__main__":
    main()