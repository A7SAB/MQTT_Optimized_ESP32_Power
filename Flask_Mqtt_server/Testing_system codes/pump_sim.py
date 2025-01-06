import paho.mqtt.client as mqtt
import json
import time
import random
import threading
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class PumpSimulator:
    def __init__(self, broker="broker.hivemq.com", port=1883):
        self.pump_id = f"PUMP_{random.randint(1000, 9999)}"
        logging.info(f"Initializing pump with ID: {self.pump_id}")
        
        # MQTT client setup
        self.client = mqtt.Client(self.pump_id, clean_session=True)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect  # Added this line
        
        # Pump state
        self.tank_height = 100  # Default tank height in cm
        self.water_distance = random.uniform(40, 60)  # Distance from sensor to water in cm
        self.last_reported_distance = self.water_distance
        self.is_running = False
        self.is_connected = False
        self.is_configured = False
        self.distance_change_threshold = 0.5  # Only report changes greater than 0.5 cm
        
        # Connect to broker
        try:
            logging.info(f"Connecting to broker at {broker}:{port}")
            self.client.connect(broker, port, 60)
            self.client.loop_start()
        except Exception as e:
            logging.error(f"Failed to connect to broker: {e}")
            raise
            
    def on_disconnect(self, client, userdata, rc):  # Added this method
        """Handle disconnection from MQTT broker"""
        self.is_connected = False
        if rc != 0:
            logging.warning("Unexpected disconnection. Attempting to reconnect...")
            time.sleep(5)
            try:
                self.client.reconnect()
            except Exception as e:
                logging.error(f"Reconnection failed: {e}")
        else:
            logging.info("Disconnected normally from broker")

    def on_connect(self, client, userdata, flags, rc):
        """Handle connection to MQTT broker"""
        if rc == 0:
            self.is_connected = True
            logging.info(f"Connected to MQTT broker")
            
            # Subscribe to topics
            topics = [
                ('mynode/pump_auth', 1),
                ('mynode/pump_control', 1),
                (f'mynode/{self.pump_id}/control', 1)
            ]
            self.client.subscribe(topics)
            
            # Initial auth request
            self.send_auth_request()
        else:
            error_messages = {
                1: "Incorrect protocol version",
                2: "Invalid client identifier",
                3: "Server unavailable",
                4: "Bad username or password",
                5: "Not authorized"
            }
            logging.error(f"Connection failed: {error_messages.get(rc, f'Unknown error {rc}')}")
            
    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages"""
        try:
            payload = json.loads(msg.payload.decode())
            logging.info(f"Received message on {msg.topic}: {payload}")
            
            if msg.topic in ['mynode/pump_control', f'mynode/{self.pump_id}/control']:
                if payload.get('device_id') == self.pump_id:
                    self.handle_control(payload)
            elif msg.topic == 'mynode/pump_auth':
                if payload.get('device_id') == self.pump_id:
                    self.handle_auth_response(payload)
                
        except json.JSONDecodeError as e:
            logging.error(f"Invalid message format on {msg.topic}: {e}")
        except Exception as e:
            logging.error(f"Error processing message: {e}")
            
    def handle_control(self, payload):
        """Handle pump control commands"""
        command = payload.get('command')
        if command in ['on', 'off']:
            new_state = (command == 'on')
            if new_state != self.is_running:
                self.is_running = new_state
                logging.info(f"Pump {'started' if self.is_running else 'stopped'}")
                self.publish_status()
                self.publish_reading(force=True)

            
    def handle_auth_response(self, payload):
        """Handle authentication responses"""
        if payload.get('device_id') != self.pump_id:
            return
                
        status = payload.get('status')
        if status == 'confirmed':
            was_configured = self.is_configured
            self.is_configured = payload.get('configured', False)
            if was_configured != self.is_configured:
                logging.info(f"Configuration status changed to: {self.is_configured}")
        elif status == 'registered':
            logging.info("Successfully registered with broker")
            
    def send_auth_request(self):
        """Send authentication request"""
        if not self.is_connected:
            return
            
        auth_msg = {
            "device_id": self.pump_id,
            "status": "new",
            "timestamp": datetime.now().isoformat()
        }
        
        logging.info("Sending authentication request")
        self.client.publish("mynode/pump_auth", json.dumps(auth_msg), qos=1)
        
    def should_report_distance(self, new_distance, force=False):
        """Determine if water distance change is significant enough to report"""
        if force or self.last_reported_distance is None:
            return True
            
        change = abs(new_distance - self.last_reported_distance)
        return change >= self.distance_change_threshold
        
    def publish_reading(self, force=False):
        """Publish water distance reading if significant change"""
        if not self.is_connected:
            return
            
        try:
            if self.should_report_distance(self.water_distance, force):
                message = {
                    "device_id": self.pump_id,
                    "water_level": round(self.water_distance, 2),
                    "value": round(self.water_distance, 2),
                    "reading": round(self.water_distance, 2),
                    "is_running": self.is_running,
                    "timestamp": datetime.now().isoformat()
                }
                
                self.client.publish("mynode/water_level", json.dumps(message), qos=1)
                self.client.publish(f"mynode/{self.pump_id}/level", json.dumps(message), qos=1)
                
                logging.info(f"Published water level: {self.water_distance:.1f}cm")
                self.last_reported_distance = self.water_distance
                
        except Exception as e:
            logging.error(f"Error publishing water level: {e}")
            
    def publish_status(self):
        """Publish pump status on change"""
        if not self.is_connected:
            return
            
        try:
            status_msg = {
                "device_id": self.pump_id,
                "status": "on" if self.is_running else "off",
                "is_running": self.is_running,
                "timestamp": datetime.now().isoformat()
            }
            
            self.client.publish("mynode/pump_status", json.dumps(status_msg), qos=1)
            self.client.publish(f"mynode/{self.pump_id}/status", json.dumps(status_msg), qos=1)
            
        except Exception as e:
            logging.error(f"Error publishing status: {e}")
        
    def update_water_distance(self):
        """Update distance measurement based on pump state"""
        if self.is_running:
            # Distance increases as water level drops (pump running)
            self.water_distance = min(self.tank_height, 
                                    self.water_distance + random.uniform(0.3, 0.7))
        else:
            # Distance decreases as water level rises (pump off)
            self.water_distance = max(0, 
                                    self.water_distance - random.uniform(0.1, 0.3))
            
        self.water_distance = round(self.water_distance, 2)
        self.publish_reading()
        
    def run(self):
        """Main pump simulation loop"""
        logging.info("Starting pump simulation")
        update_interval =  1 # Check every second
        
        try:
            while True:
                if self.is_connected:
                    self.update_water_distance()
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            logging.info("Stopping pump simulation")
            self.client.disconnect()
            raise
        except Exception as e:
            logging.error(f"Error in pump simulation: {e}")
            self.client.disconnect()
            raise

def main():
    """Main function to run a single pump simulator"""
    try:
        pump = PumpSimulator()
        pump.run()
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
    finally:
        logging.info("Cleanup complete")

if __name__ == "__main__":
    main()