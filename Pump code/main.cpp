#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_system.h"

// Pin Configuration
const uint8_t RELAY_PIN = 32;  // Active LOW
const uint8_t TRIG_PIN = 12;   // Ultrasonic trigger
const uint8_t ECHO_PIN = 14;   // Ultrasonic echo

// Constants
const float SOUND_SPEED = 0.034;           // Speed of sound in cm/microsecond
const float TANK_HEIGHT = 100.0;           // Maximum tank height in cm
const float DISTANCE_THRESHOLD = 0.5;      // Minimum change to report (cm)
const uint32_t CONFIG_WAIT_TIME = 20000;   // Wait time for config retry (ms)
const uint32_t TASK_TIMEOUT = 10000;       // Task watchdog timeout (ms)
const uint32_t MQTT_RETRY_DELAY = 5000;    // MQTT reconnection delay (ms)
const uint32_t ULTRASONIC_TIMEOUT = 30000; // Ultrasonic reading timeout (µs)

// WiFi Configuration
const char* WIFI_SSID = "MH_EXT";
const char* WIFI_PASS = "MH19283746";

// MQTT Configuration
const char* MQTT_SERVER = "broker.hivemq.com";
const uint16_t MQTT_PORT = 1883;
const char* TOPIC_AUTH = "mynode/pump_auth";
const char* TOPIC_CONTROL = "mynode/pump_control";
const char* TOPIC_LEVEL = "mynode/water_level";
const char* TOPIC_STATUS = "mynode/pump_status";

// Task handles and synchronization
TaskHandle_t ultrasonicTaskHandle = nullptr;
TaskHandle_t mqttTaskHandle = nullptr;
SemaphoreHandle_t distanceMutex = nullptr;

// Global variables
WiFiClient espClient;
PubSubClient mqttClient(espClient);
char deviceId[16];  // Buffer for device ID
float currentDistance = 0.0;
float lastReportedDistance = 0.0;
float sensorOffset = 0.0;  // Calibration offset
bool pumpRunning = false;
bool deviceConfigured = false;
volatile bool configRequested = false;
uint32_t lastConfigTime = 0;

// Watchdog timestamps
volatile uint32_t lastUltrasonicUpdate = 0;
volatile uint32_t lastMqttUpdate = 0;

// Function declarations
void setupHardware();
void setupWiFi();
bool connectMQTT();
void handleMqttMessage(char* topic, byte* payload, unsigned int length);
float readDistance();
void publishWaterLevel(bool force = false);
void publishPumpStatus();
void requestConfig();
void monitorTasks();

// Debug print function
void debugPrint(const char* msg, bool newline = true) {
    Serial.print(msg);
    if (newline) Serial.println();
}

// Ultrasonic sensor measurement implementation
float readDistance() {
    // Clear trigger
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(2);
    
    // Send pulse
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);
    
    // Measure response with timeout
    unsigned long duration = pulseIn(ECHO_PIN, HIGH, ULTRASONIC_TIMEOUT);
    
    if (duration == 0) {
        debugPrint("Ultrasonic timeout");
        return lastReportedDistance;
    }
    
    // Calculate distance with offset
    float distance = (duration * SOUND_SPEED / 2.0) + sensorOffset;
    return constrain(distance, 0, TANK_HEIGHT);
}

// MQTT message callback
void handleMqttMessage(char* topic, byte* payload, unsigned int length) {
    // Ensure null termination
    char* message = (char*)malloc(length + 1);
    memcpy(message, payload, length);
    message[length] = '\0';
    
    StaticJsonDocument<200> doc;
    DeserializationError error = deserializeJson(doc, message);
    free(message);
    
    if (error) {
        debugPrint("JSON parse failed");
        return;
    }
    
    // Verify message is for this device
    const char* targetId = doc["device_id"];
    if (!targetId || strcmp(targetId, deviceId) != 0) {
        return;
    }
    
    if (strcmp(topic, TOPIC_AUTH) == 0) {
        const char* status = doc["status"];
        if (status && strcmp(status, "confirmed") == 0) {
            deviceConfigured = doc["configured"] | false;
            debugPrint(deviceConfigured ? "Device configured" : "Config needed");
        }
    } 
    else if (strstr(topic, "control")) {
        const char* cmd = doc["command"];
        if (!cmd) return;
        
        bool newState = (strcmp(cmd, "on") == 0);
        if (newState != pumpRunning) {
            pumpRunning = newState;
            digitalWrite(RELAY_PIN, !pumpRunning);  // Active LOW
            publishPumpStatus();
            publishWaterLevel(true);
        }
    }
}

// Ultrasonic measurement task
void ultrasonicTask(void* parameter) {
    const TickType_t delay = pdMS_TO_TICKS(1000);
    
    while (true) {
        if (xSemaphoreTake(distanceMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
            currentDistance = readDistance();
            xSemaphoreGive(distanceMutex);
            
            publishWaterLevel();
            lastUltrasonicUpdate = millis();
        }
        
        vTaskDelay(delay);
    }
}

// MQTT management task
void mqttTask(void* parameter) {
    const TickType_t delay = pdMS_TO_TICKS(10);
    
    while (true) {
        if (!mqttClient.connected() && !connectMQTT()) {
            vTaskDelay(pdMS_TO_TICKS(MQTT_RETRY_DELAY));
            continue;
        }
        
        mqttClient.loop();
        lastMqttUpdate = millis();
        vTaskDelay(delay);
    }
}

void publishWaterLevel(bool force) {
    if (!mqttClient.connected()) return;
    
    float distance;
    if (xSemaphoreTake(distanceMutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        distance = currentDistance;
        xSemaphoreGive(distanceMutex);
    } else {
        return;
    }
    
    if (!force && abs(distance - lastReportedDistance) < DISTANCE_THRESHOLD) {
        return;
    }
    
    StaticJsonDocument<200> doc;
    doc["device_id"] = deviceId;
    doc["water_level"] = distance;
    doc["is_running"] = pumpRunning;
    
    char buffer[256];
    serializeJson(doc, buffer);
    
    if (mqttClient.publish(TOPIC_LEVEL, buffer)) {
        lastReportedDistance = distance;
    }
}

void publishPumpStatus() {
    if (!mqttClient.connected()) return;
    
    StaticJsonDocument<200> doc;
    doc["device_id"] = deviceId;
    doc["status"] = pumpRunning ? "on" : "off";
    doc["is_running"] = pumpRunning;
    
    char buffer[256];
    serializeJson(doc, buffer);
    mqttClient.publish(TOPIC_STATUS, buffer);
}

void requestConfig() {
    if (!configRequested || (millis() - lastConfigTime > CONFIG_WAIT_TIME)) {
        StaticJsonDocument<200> doc;
        doc["device_id"] = deviceId;
        doc["status"] = "new";
        
        char buffer[256];
        serializeJson(doc, buffer);
        
        if (mqttClient.publish(TOPIC_AUTH, buffer)) {
            configRequested = true;
            lastConfigTime = millis();
        }
    }
}

void setupHardware() {
    pinMode(RELAY_PIN, OUTPUT);
    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);
    digitalWrite(RELAY_PIN, HIGH);  // Ensure pump starts OFF
}

void setupWiFi() {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    debugPrint("\nWiFi connected");
}

bool connectMQTT() {
    if (!mqttClient.connect(deviceId)) {
        return false;
    }
    
    mqttClient.subscribe(TOPIC_AUTH);
    mqttClient.subscribe(TOPIC_CONTROL);
    
    char topic[32];
    snprintf(topic, sizeof(topic), "mynode/%s/control", deviceId);
    mqttClient.subscribe(topic);
    
    if (!deviceConfigured) {
        requestConfig();
    }
    
    return true;
}

void monitorTasks() {
    uint32_t now = millis();
    
    // Check ultrasonic task
    if (now - lastUltrasonicUpdate > TASK_TIMEOUT && ultrasonicTaskHandle != nullptr) {
        debugPrint("Restarting ultrasonic task");
        vTaskDelete(ultrasonicTaskHandle);
        ultrasonicTaskHandle = nullptr;
        
        xTaskCreatePinnedToCore(
            ultrasonicTask,
            "Ultrasonic",
            4096,
            nullptr,
            1,
            &ultrasonicTaskHandle,
            0
        );
    }
    
    // Check MQTT task
    if (now - lastMqttUpdate > TASK_TIMEOUT && mqttTaskHandle != nullptr) {
        debugPrint("Restarting MQTT task");
        vTaskDelete(mqttTaskHandle);
        mqttTaskHandle = nullptr;
        
        xTaskCreatePinnedToCore(
            mqttTask,
            "MQTT",
            4096,
            nullptr,
            2,
            &mqttTaskHandle,
            1
        );
    }
}

void setup() {
    Serial.begin(115200);
    debugPrint("\nESP32 Pump Controller Starting");
    
    // Generate unique device ID
    snprintf(deviceId, sizeof(deviceId), "PUMP_%04X", (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
    debugPrint(deviceId);
    
    setupHardware();
    
    // Create mutex for distance measurements
    distanceMutex = xSemaphoreCreateMutex();
    if (!distanceMutex) {
        debugPrint("Mutex creation failed");
        while(1) delay(1000);
    }
    
    setupWiFi();
    
    // Setup MQTT
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(handleMqttMessage);
    mqttClient.setSocketTimeout(10);
    
    // Create tasks
    BaseType_t taskCreated = xTaskCreatePinnedToCore(
        ultrasonicTask,
        "Ultrasonic",
        4096,
        nullptr,
        1,
        &ultrasonicTaskHandle,
        0
    );
    
    if (taskCreated != pdPASS) {
        debugPrint("Failed to create ultrasonic task");
        while(1) delay(1000);
    }
    
    taskCreated = xTaskCreatePinnedToCore(
        mqttTask,
        "MQTT",
        4096,
        nullptr,
        2,
        &mqttTaskHandle,
        1
    );
    
    if (taskCreated != pdPASS) {
        debugPrint("Failed to create MQTT task");
        while(1) delay(1000);
    }
    
    debugPrint("Setup complete");
}

void loop() {
    monitorTasks();
    vTaskDelay(pdMS_TO_TICKS(1000));
}
