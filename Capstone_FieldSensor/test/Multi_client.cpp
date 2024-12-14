#include <Arduino.h>
#include <WiFi.h>
#include <WiFiManager.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>

// Default settings
char mqtt_id[40] = "ESP32C3_Sensor";
char sensor_location[40] = "room1";
uint32_t sleep_time = 30; // Default 30 seconds

// MQTT Broker Settings
#define MQTT_SERVER "broker.hivemq.com"
#define MQTT_PORT 1883

// DHT Sensor Configuration
#define DHTPIN 2
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// Objects
WiFiClient espClient;
PubSubClient mqttClient(espClient);
Preferences preferences;

// Topics
String MQTT_CONFIG_TOPIC;
String MQTT_DATA_TOPIC;

// Custom HTML for WiFiManager
const char* custom_html = R"(
<div class="container">
  <h2>Sensor Configuration</h2>
  <div class="form-group">
    <label for="mqttId">Device ID</label>
    <input type="text" id="mqttId" name="mqttId" required>
    
    <label for="location">Location</label>
    <input type="text" id="location" name="location" required>
    
    <label for="sleepTime">Sleep Time (seconds)</label>
    <input type="number" id="sleepTime" name="sleepTime" min="5" required>
  </div>
</div>
)";

void loadConfig() {
    preferences.begin("sensor", false);
    sleep_time = preferences.getUInt("sleep_time", 30);
    preferences.getString("mqtt_id", mqtt_id, sizeof(mqtt_id));
    preferences.getString("location", sensor_location, sizeof(sensor_location));
    preferences.end();
    
    // Set up MQTT topics
    MQTT_CONFIG_TOPIC = String("sensor/") + mqtt_id + "/config";
    MQTT_DATA_TOPIC = String("sensor/") + mqtt_id + "/data";
}

void saveConfig() {
    preferences.begin("sensor", false);
    preferences.putUInt("sleep_time", sleep_time);
    preferences.putString("mqtt_id", mqtt_id);
    preferences.putString("location", sensor_location);
    preferences.end();
}

void callback(char* topic, byte* payload, unsigned int length) {
    StaticJsonDocument<200> doc;
    deserializeJson(doc, payload, length);
    
    if (doc.containsKey("sleep_time")) {
        sleep_time = doc["sleep_time"];
        saveConfig();
    }
}

void setupWiFiManager() {
    WiFiManager wm;
    
    // Create custom parameters
    WiFiManagerParameter custom_mqtt_id("mqttId", "Device ID", mqtt_id, 40);
    WiFiManagerParameter custom_location("location", "Location", sensor_location, 40);
    char sleep_str[8];
    itoa(sleep_time, sleep_str, 10);
    WiFiManagerParameter custom_sleep("sleepTime", "Sleep Time", sleep_str, 8);
    
    wm.addParameter(&custom_mqtt_id);
    wm.addParameter(&custom_location);
    wm.addParameter(&custom_sleep);
    
    wm.setSaveParamsCallback([&]() {
        strcpy(mqtt_id, custom_mqtt_id.getValue());
        strcpy(sensor_location, custom_location.getValue());
        sleep_time = atoi(custom_sleep.getValue());
        saveConfig();
    });
    
    bool res = wm.autoConnect("ESP32_Sensor_Setup", "password123");
    
    if (!res) {
        ESP.restart();
    }
}

void publishData() {
    float temperature = dht.readTemperature();
    float humidity = dht.readHumidity();
    
    if (isnan(temperature) || isnan(humidity)) {
        return;
    }
    
    StaticJsonDocument<200> doc;
    doc["device_id"] = mqtt_id;
    doc["location"] = sensor_location;
    doc["temperature"] = temperature;
    doc["humidity"] = humidity;
    doc["timestamp"] = millis();
    
    char jsonBuffer[512];
    serializeJson(doc, jsonBuffer);
    mqttClient.publish(MQTT_DATA_TOPIC.c_str(), jsonBuffer);
}

void connectMQTT() {
    while (!mqttClient.connected()) {
        if (mqttClient.connect(mqtt_id)) {
            mqttClient.subscribe(MQTT_CONFIG_TOPIC.c_str());
        } else {
            delay(5000);
        }
    }
}

void setup() {
    Serial.begin(115200);
    dht.begin();
    
    loadConfig();
    setupWiFiManager();
    
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(callback);
}

void loop() {
    if (!mqttClient.connected()) {
        connectMQTT();
    }
    mqttClient.loop();
    
    publishData();
    esp_sleep_enable_timer_wakeup(sleep_time * 1000000ULL);
    esp_deep_sleep_start();
}