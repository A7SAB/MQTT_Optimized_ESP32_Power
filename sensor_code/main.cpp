#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <ArduinoOTA.h>
#include "FS.h"
#include <WebServer.h>
#include <DNSServer.h>
#include <OneWire.h>
#include <DallasTemperature.h>


// WiFi credentials
const char* ssid = "YOUR WIFI SSID";
const char* password = "YOUR WIFI PASSWORD";

// MQTT Broker settings
const char* mqtt_server = "broker.hivemq.com";
const int mqtt_port = 1883;

// MQTT Topics
const char* MQTT_TOPIC_TEMP = "mynode/Temperature";
const char* MQTT_TOPIC_HUM = "mynode/moisture";
const char* MQTT_TOPIC_AUTH = "mynode/auth";
const char* MQTT_TOPIC_ACK = "mynode/ack";
const char* MQTT_TOPIC_SLEEP = "mynode/default/config/sleep";

// LED Pin
const int ledPin = 8;

// Soil Moisture Sensor
int _moisture, sensor_analog;
const int sensor_pin = A1;  


// DS18B20 Temperature Sensor
const int oneWireBus = A2;     // GPIO where the DS18B20 is connected to
OneWire oneWire(oneWireBus);
DallasTemperature sensors(&oneWire);


// States
enum State {
    STATE_INIT,
    STATE_AUTH,
    STATE_GET_SLEEP,
    STATE_PUBLISH,
    STATE_WAIT_ACK,
    STATE_SLEEP
};

// Global variables
String deviceId;
uint32_t sleepDuration = 30;  // Default sleep duration in seconds
uint32_t lastMessageTime = 0;
uint32_t startTime = 0;
State currentState = STATE_INIT;
bool isAuthenticated = false;
bool dataAcknowledged = false;
bool dataSent = false;
bool sleepTimeReceived = false;

// Initialize objects
WiFiClient espClient;
PubSubClient mqtt(espClient);
Preferences preferences;

void setState(State newState) {
    currentState = newState;
    lastMessageTime = millis();
    Serial.printf("[STATE] Changed to state: %d\n", newState);
}

void connectWiFi() {
    Serial.printf("[WIFI] Connecting to %s\n", ssid);
    WiFi.begin(ssid, password);
    
    uint32_t startAttempt = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 30000) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();
    
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WIFI] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("[WIFI] Connection failed! Going to sleep.");
        ESP.deepSleep(60 * 1000000ULL);
    }
}

void requestSleepTime() {
    StaticJsonDocument<200> doc;
    doc["device_id"] = deviceId;
    doc["action"] = "get_sleep_time";

    char jsonBuffer[200];
    serializeJson(doc, jsonBuffer);
    
    if (mqtt.publish(MQTT_TOPIC_SLEEP, jsonBuffer)) {
        Serial.println("[SLEEP] Sleep time request sent");
        lastMessageTime = millis();
    } else {
        Serial.println("[ERROR] Failed to request sleep time");
    }
}

void handleCallback(char* topic, byte* payload, unsigned int length) {
    Serial.printf("[MQTT] Message received on topic: %s\n", topic);
    
    char message[256];
    memcpy(message, payload, min(length, sizeof(message) - 1));
    message[min(length, sizeof(message) - 1)] = '\0';
    Serial.printf("[MQTT] Message content: %s\n", message);
    
    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, message);
    if (error) {
        Serial.printf("[ERROR] JSON parse failed: %s\n", error.c_str());
        return;
    }

    String topicStr = String(topic);
    // Handle authentication response
    if (topicStr == MQTT_TOPIC_AUTH) {
        if (doc.containsKey("status") && doc["status"] == "approved") {
            Serial.println("[AUTH] Device authenticated!");
            isAuthenticated = true;
            setState(STATE_GET_SLEEP);
        }
    }
    // Handle sleep time response
    else if (topicStr == MQTT_TOPIC_SLEEP) {
        if (doc.containsKey("sleep_time") && doc["device_id"] == deviceId) {
            uint32_t newSleepTime = doc["sleep_time"];
            if (newSleepTime > 0) {
                sleepDuration = newSleepTime;
                preferences.putUInt("sleep", sleepDuration);
                Serial.printf("[SLEEP] Updated sleep time to: %d seconds\n", sleepDuration);
                sleepTimeReceived = true;
                setState(STATE_PUBLISH);
            }
        }
    }
    // Handle data acknowledgment
    else if (topicStr == MQTT_TOPIC_ACK) {
        if (doc.containsKey("status") && doc["status"] == "received") {
            Serial.println("[ACK] Data acknowledged by server");
            dataAcknowledged = true;
            setState(STATE_SLEEP);
        }
    }
}

void setupMQTT() {
    mqtt.setServer(mqtt_server, mqtt_port);
    mqtt.setCallback(handleCallback);
    
    deviceId = "ESP32_" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.printf("[MQTT] Device ID: %s\n", deviceId.c_str());
    
    Serial.println("[MQTT] Connecting...");
    if (mqtt.connect(deviceId.c_str())) {
        Serial.println("[MQTT] Connected!");
        mqtt.subscribe(MQTT_TOPIC_AUTH);
        mqtt.subscribe(MQTT_TOPIC_ACK);
        mqtt.subscribe(MQTT_TOPIC_SLEEP);
    } else {
        Serial.printf("[MQTT] Connection failed, rc=%d\n", mqtt.state());
    }
}

void requestAuthentication() {
    StaticJsonDocument<200> doc;
    doc["device_id"] = deviceId;
    doc["action"] = "auth_request";

    char jsonBuffer[200];
    serializeJson(doc, jsonBuffer);
    
    if (mqtt.publish(MQTT_TOPIC_AUTH, jsonBuffer)) {
        Serial.println("[AUTH] Authentication request sent");
        lastMessageTime = millis();
    }
}

void publishSensorData() {
    if (dataSent) return;

    delay(1000);
    sensor_analog = analogRead(sensor_pin);
    _moisture = (100 - ((sensor_analog / 4095.0) * 100));
    
    sensors.requestTemperatures(); 
    float temperature = sensors.getTempCByIndex(0);
    
    if (temperature == -127.00) {
        Serial.println("[ERROR] Failed to read Temperature Sensor!");
        return;
    }

    StaticJsonDocument<200> doc;
    doc["device_id"] = deviceId;
    doc["temperature"] = temperature;
    doc["moisture"] = _moisture;

    char jsonBuffer[200];
    serializeJson(doc, jsonBuffer);
    
     if (mqtt.publish(MQTT_TOPIC_TEMP, jsonBuffer) && 
        mqtt.publish(MQTT_TOPIC_HUM, jsonBuffer)) {
        Serial.printf("[DATA] Published - T: %.1f°C, H: %.1f%%\n", temperature, _moisture);
        dataSent = true;
        setState(STATE_WAIT_ACK);
    } else {
        Serial.println("[ERROR] Failed to publish data");
    }
}

void setup() {
    Serial.begin(115200);
    delay(100);
    Serial.println("\n[INIT] Starting ESP32 Sensor Node");
    pinMode(ledPin, OUTPUT);
    
    startTime = millis();
    preferences.begin("sensor", false);
    
    // Load saved sleep duration
    sleepDuration = preferences.getUInt("sleep", 30);
    Serial.printf("[INIT] Loaded sleep duration: %d seconds\n", sleepDuration);
    
    connectWiFi();
    setupMQTT();
    setState(STATE_AUTH);
}

void loop() {
    mqtt.loop();

    // Check for timeout (2 minutes total runtime)
    if (millis() - startTime > 120000) {
        Serial.println("[TIMEOUT] Total runtime exceeded. Going to sleep.");
        ESP.deepSleep(sleepDuration * 1000000ULL);
        return;
    }

    // Connection check
    if (WiFi.status() != WL_CONNECTED || !mqtt.connected()) {
        Serial.println("[ERROR] Connection lost. Going to sleep.");
        ESP.deepSleep(sleepDuration * 1000000ULL);
        return;
    }

    // State machine
    switch (currentState) {
        case STATE_AUTH:
            if (!isAuthenticated && millis() - lastMessageTime > 5000) {
                requestAuthentication();
            }
            break;
            
        case STATE_GET_SLEEP:
            if (!sleepTimeReceived && millis() - lastMessageTime > 5000) {
                requestSleepTime();
            }
            // Timeout after 30 seconds and proceed with current sleep time
            if (!sleepTimeReceived && millis() - lastMessageTime > 30000) {
                Serial.println("[SLEEP] No sleep time received, using current value");
                setState(STATE_PUBLISH);
            }
            break;
            
        case STATE_PUBLISH:
            if (!dataSent) {
                publishSensorData();
            }
            break;
            
        case STATE_WAIT_ACK:
            if (!dataAcknowledged && millis() - lastMessageTime > 10000) {
                Serial.println("[TIMEOUT] No acknowledgment received. Going to sleep.");
                ESP.deepSleep(sleepDuration * 1000000ULL);
            }
            break;
            
        case STATE_SLEEP:
            Serial.printf("[SUCCESS] Complete cycle finished. Sleeping for %d seconds...\n", sleepDuration);
            ESP.deepSleep(sleepDuration * 1000000ULL);
            break;
            
        default:
            break;
    }
}

