# MQTT Optimized ESP32 Power

Welcome to the **MQTT Optimized ESP32 Power** project repository! This repository hosts the source code and documentation for **Group Project P01G01**, focused on optimizing power usage for ESP32 devices with MQTT communication. 

---

## **Project Overview**

This project demonstrates an MQTT-based system using an ESP32 microcontroller and a DHT11 sensor for measuring humidity and temperature. The goal is to develop an efficient and interactive IoT solution.

### **Features**
- **ESP32 Integration**: Low-power consumption with optimized operations.
- **DHT11 Sensor**: Real-time monitoring of humidity and temperature.

---

## **Repository Structure**

- **`Capstone_FieldSensor`**: for the ESP code
- **`Flask_Mqtt_Version_1`**: the file for the mqtt and web server
- **`Flask_Mqtt_Version_1/requirements.txt`**: Dependencies required to run the project.

---

## **Getting Started**

Follow the steps below to set up the project on your local machine:

### **Clone the Repository**
```bash
git clone https://github.com/A7SAB/MQTT_Optimized_ESP32_Power.git
cd MQTT_Optimized_ESP32_Power
```

### **Run the Server**

#### Step 1: Create a Virtual Environment
Set up a virtual environment to manage dependencies:
```bash
# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
# For Windows
venv\Scripts\activate

# For macOS/Linux
source venv/bin/activate
```

#### Step 2: Install Dependencies
Install the required Python packages:
```bash
pip install -r requirements.txt
```

#### Step 3: Start the Server
Run the application:
```bash
python app.py
```

Access the application at [http://0.0.0.0:5000](http://0.0.0.0:5000).

Username: admin
Password: admin123

---

## **Hardware Requirements**

### Components:
- **ESP32**: Microcontroller for IoT functionality.
- **DHT11**: Humidity and temperature sensor.

### Circuit Diagram:
_Coming Soon!_

---

## **Future Enhancements**
Here are some features we're working on:
- **Database Integration**: Store sensor readings and user data persistently.
- **Full Web Interface**: Add user-friendly features like login pages and dashboards.
- **Mobile Compatibility**: Responsive design for mobile devices.
- **Sensors Manging dashboard**: Adjust Each sensor sleeping time
- **Wifi manager** : To manage the wifi connectiivty 
---

