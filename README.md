# MQTT Optimized ESP32 Power

Welcome to the **MQTT Optimized ESP32 Power** project repository! This repository hosts the source code and documentation for **Group Project P01G01**, focused on optimizing power usage for ESP32 devices with MQTT communication. 

---

## **Project Overview**

This project demonstrates an MQTT-based system using an ESP32 microcontroller and a DHT11 sensor for measuring humidity and temperature. The goal is to develop an efficient and interactive IoT solution.

### **Features**
- Low-power consumption with optimized operations.
- Real-time monitoring of soil moisture and soil temperature.
- Water management system with pump integration.

---

## **Repository Structure**

- **`Pump Code`**: Code for Pump Node
- **`Sensor_Code`**: Code for Sensor Node
- **`Flask_Mqtt_Server`**: the file for the mqtt and web server
- **`Flask_Mqtt_Server/requirements.txt`**: Dependencies required to run the project.

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
- **Ultrasonic sensor**: Measuering the water capacity.
- **Soil Moisture Sensor**: Measuring the soil Moisture.
- **Temperture sensor**: Measuering the temperture sensor.


## **Future Enhancements**
Here are some features we're working on:
- **Notification Manger**: Adding the ability to manage the notfications. 
- **WIFI Manager**: to make the connectivity more user friendly. 
---

