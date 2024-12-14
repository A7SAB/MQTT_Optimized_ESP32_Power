#include <Arduino.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <Adafruit_Sensor.h>
#include <ArduinoOTA.h>
#include <DHT.h>
#include <PubSubClient.h>  // Changed from AsyncMqttClient as it's more stable on ESP32

// MQTT Broker Settings (Fixed)
#define MQTT_SERVER "broker.hivemq.com"
#define MQTT_PORT 1883
#define MQTT_ID "ESP32C3_Sensor"
#define MQTT_PUB_TEMP "mynode/Temperature"
#define MQTT_PUB_HUM "mynode/Humidity"

// DHT Sensor Configuration
#define DHTPIN 2  // Verify this pin number for your ESP32-C3 board
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// MQTT Client setup
WiFiClient espClient;
PubSubClient mqttClient(espClient);

// State variables for sequential tasks
enum TaskState { WIFI_TASK, MQTT_TASK, PUBLISH_TASK, FINISH_TASK };
TaskState currentTask = WIFI_TASK;
bool wifiConnected = false;
bool mqttConnected = false;
bool dataPublished = false;

// Deep Sleep Duration (in microseconds)
#define DEEP_SLEEP_DURATION 30e6 // 30 seconds

// Function to setup WiFiManager
void setupWiFiManager() {
  WiFiManager wm;
  
  // Configure reset button pin (for ESP32-C3)
  const int TRIGGER_PIN = 9;  // Changed to GPIO9 which is often used as a button on ESP32-C3
  pinMode(TRIGGER_PIN, INPUT);
  
  // Reset settings if trigger pin is pressed
  if (digitalRead(TRIGGER_PIN) == LOW) {
    Serial.println("Resetting WiFi Settings...");
    wm.resetSettings();
  }
  
  // Set config portal timeout
  wm.setConfigPortalTimeout(180);
  
  // Customize the web portal
  wm.setTitle("ESP32-C3 WiFi Setup");
  
  // Attempt to connect using saved credentials or start config portal
  bool res = wm.autoConnect("ESP32C3_Setup", "password123");
  
  if (!res) {
    Serial.println("Failed to connect");
    ESP.restart();
  }
  
  Serial.println("Connected to WiFi");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
  
  wifiConnected = true;
}

// MQTT callback function
void callback(char* topic, byte* payload, unsigned int length) {
  // Handle incoming messages if needed
}

// Function to connect to MQTT server
void connectToMqtt() {
  Serial.println("Connecting to MQTT...");
  
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setCallback(callback);
  
  while (!mqttClient.connected()) {
    Serial.print(".");
    
    if (mqttClient.connect(MQTT_ID)) {
      Serial.println("\nConnected to MQTT broker");
      mqttConnected = true;
    } else {
      Serial.print("\nFailed with state ");
      Serial.print(mqttClient.state());
      delay(2000);
    }
  }
}

// Function to publish data to MQTT
void publishData() {
  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();
  
  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("Failed to read from DHT sensor!");
    return;
  }
  
  // Convert float to string
  char tempStr[8];
  char humStr[8];
  dtostrf(temperature, 1, 2, tempStr);
  dtostrf(humidity, 1, 2, humStr);
  
  // Publish to MQTT
  bool tempPublished = mqttClient.publish(MQTT_PUB_TEMP, tempStr);
  bool humPublished = mqttClient.publish(MQTT_PUB_HUM, humStr);
  
  Serial.printf("Temperature: %s°C, Humidity: %s%%\n", tempStr, humStr);
  Serial.printf("Publish success - Temp: %d, Hum: %d\n", tempPublished, humPublished);
  
  if (tempPublished && humPublished) {
    dataPublished = true;
  }
}

// Function to execute the current task
void runTask() {
  switch (currentTask) {
    case WIFI_TASK:
      if (!wifiConnected) {
        setupWiFiManager();
      }
      if (wifiConnected) {
        currentTask = MQTT_TASK;
      }
      break;
      
    case MQTT_TASK:
      if (wifiConnected) {
        connectToMqtt();
        if (mqttConnected) {
          currentTask = PUBLISH_TASK;
        }
      }
      break;
      
    case PUBLISH_TASK:
      if (mqttConnected) {
        publishData();
        if (dataPublished) {
          currentTask = FINISH_TASK;
        }
      }
      break;
      
    case FINISH_TASK:
      Serial.println("All tasks finished. Entering deep sleep...");
      esp_deep_sleep(DEEP_SLEEP_DURATION);
      break;
  }
}

void setup() {
  Serial.begin(115200);
  dht.begin();
}

void loop() {
  runTask();
  
  // Keep MQTT connection alive
  if (mqttConnected) {
    mqttClient.loop();
  }
}