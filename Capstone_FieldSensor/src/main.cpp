#include <Arduino.h>
#include <WiFi.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <AsyncMqttClient.h>
#include <ArduinoOTA.h>
#include <WebServer.h>

// Wi-Fi Credentials
#define WIFI_SSID "****"
#define WIFI_PASSWORD "**"

// MQTT Broker
#define MQTT_SERVER "broker.hivemq.com"
#define MQTT_PORT 1883
#define MQTT_PUB_TEMP "mynode/Temperature"
#define MQTT_PUB_HUM "mynode/Humidity"

// DHT Sensor Configuration
#define DHTPIN 2
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// MQTT Client
AsyncMqttClient mqttClient;

// State variables for sequential tasks
enum TaskState { WIFI_TASK, MQTT_TASK, PUBLISH_TASK, FINISH_TASK };
TaskState currentTask = WIFI_TASK;

bool wifiConnected = false;
bool mqttConnected = false;
bool dataPublished = false;

// Deep Sleep Duration (in microseconds)
#define DEEP_SLEEP_DURATION 30e6 // 30 sed

// Function to connect to Wi-Fi
void connectToWifi() {
  Serial.println("Connecting to Wi-Fi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWi-Fi connected!");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
  wifiConnected = true;
}

// Function to connect to MQTT server
void connectToMqtt() {
  Serial.println("Connecting to MQTT...");
  mqttClient.onConnect([](bool sessionPresent) {
    Serial.println("Connected to MQTT.");
    mqttConnected = true;
  });
  mqttClient.onDisconnect([](AsyncMqttClientDisconnectReason reason) {
    Serial.println("Disconnected from MQTT.");
    mqttConnected = false;
  });
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.connect();

  while (!mqttConnected) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nMQTT connection established.");
}

// Function to publish data to MQTT
void publishData() {
  float temperature = dht.readTemperature();
  float humidity = dht.readHumidity();

  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("Failed to read from DHT sensor!");
    return;
  }

  uint16_t packetIdTemp = mqttClient.publish(MQTT_PUB_TEMP, 1, true, String(temperature).c_str());
  uint16_t packetIdHum = mqttClient.publish(MQTT_PUB_HUM, 1, true, String(humidity).c_str());

  Serial.printf("Temperature: %.2f°C, Humidity: %.2f%%\n", temperature, humidity);
  Serial.printf("Published Temp Packet ID: %d, Hum Packet ID: %d\n", packetIdTemp, packetIdHum);

  // Simulate waiting for acknowledgment (Optional for AsyncMqttClient)
  delay(1000); // Ensure data is sent
  dataPublished = true;
}

// Function to execute the current task
void runTask() {
  switch (currentTask) {
    case WIFI_TASK:
      connectToWifi();
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

  // Initialize MQTT client
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
}

void loop() {
  runTask();
}