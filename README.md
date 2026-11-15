
/*
# 🚀 AstroPulse: Astronaut Vital Sign Monitoring System
### NASA Space Apps Challenge — *Create Health Monitoring Software for Astronauts on Space Missions*

**AstroPulse** is an integrated hardware-and-software telemetry ecosystem designed to monitor, log, and analyze astronaut vital signs during long-duration space missions. By bridging real-time microcontroller hardware (ESP32) with an intelligent Python backend and an interactive web dashboard, the system provides both immediate physiological feedback and long-term trend analysis.

---

## ✨ Key Features

* **Real-Time Biometric Telemetry:** Tracks heart rate (BPM) via the MAX30102 sensor and non-contact surface/ambient temperature via the MLX90614 sensor.
* **Dual-Display Architecture:** Live readings are rendered locally on a round GC9A01A TFT screen on the hardware device and mirrored instantly onto a web dashboard.
* **Automated Health Status Evaluations:** Automatically evaluates readings against clinical thresholds (e.g., detecting bradycardia/tachycardia ranges for heart rate and elevated fever ranges for temperature, adjusted for sensor offset).
* **Multi-Session CSV Logging:** Automatically records monitoring sessions, calculating mean, median, min, max, and sample counts, saving everything to organized CSV reports.
* **Historical Tendency Charts:** Features an interactive logs view powered by Chart.js that automatically categorizes sessions into Temperature and BPM to visualize physiological trends over time.

---

## 📂 Project Structure

```text
├── main.cpp                # Arduino C++ firmware for the ESP32
├── main.py                 # Python FastAPI backend and web dashboard
├── monitor_sessions/       # Folder where completed session CSV logs are stored
└── README.md               # Project documentation
```

## 🧹 Resetting Data & Custom Charts

The `monitor_sessions/` directory stores all completed session CSV files.

- To clear any pre-recorded sample data and **start fresh with your own live sessions, charts, and graphs**, simply **delete all files inside the `monitor_sessions/` folder** (or delete the folder itself—the application will automatically recreate it on startup).
- Once cleared, every new session you record via the hardware buttons will populate clean, custom data onto your logs and tendency charts.

## 🛠️ Hardware Requirements & Pinout

- **Microcontroller:** ESP32 NodeMCU / DevKit
- **Display:** Adafruit GC9A01A 1.28" Round TFT Display (`TFT_CS: 5`, `TFT_DC: 4`, `TFT_RST: 16`)
- **Temperature Sensor:** Adafruit MLX90614 (I2C: `SDA: 21`, `SCL: 22` at 100kHz standard speed)
- **Heart Rate Sensor:** MAX30102 / MAX30105 Particle Sensor (I2C)
- **Hardware Buttons:**
  - Temperature Mode Button: Pin `27` (configured with internal pull-up)
  - BPM Mode Button: Pin `17` (configured with internal pull-up)

## ⚙️ Setup & Installation Guide

### 1. Hardware Firmware (Arduino IDE)

1. Open the Arduino IDE and ensure you have the **ESP32 board package** installed.
2. Install the following required libraries via the Library Manager:
   - `ArduinoJson` (v6+)
   - `Adafruit GFX Library`
   - `Adafruit GC9A01A`
   - `Adafruit MLX90614 Library`
   - `SparkFun MAX3010x Library`
3. Connect your ESP32, select your correct Board and COM Port, and upload `main.cpp`.

### 2. Software Backend (Python & FastAPI)

1. Ensure you have **Python 3.8+** installed on your system.
2. Install the required Python packages:

   Bash
   ```
   pip install fastapi uvicorn pyserial pydantic
   ```

3. Connect your ESP32 to your computer via USB.
4. Run the FastAPI backend server:

   Bash
   ```
   python main.py
   ```

5. Open your web browser and navigate to: 👉 **`http://127.0.0.1:8000`**

## 🖥️ Usage Instructions

1. **Connect to Port:** Open the web dashboard, select your ESP32's COM port from the dropdown menu, and click **Switch & Connect Port**.
2. **Start Monitoring:** Press the physical hardware buttons connected to your ESP32 to toggle between Temperature Mode (Pin 27) and BPM Mode (Pin 17).
3. **View Live Data:** Watch live streams update instantly on both the hardware TFT screen and the web dashboard.
4. **Analyze Logs & Charts:** Click the **📂 View Logs & Charts** button on the top right of the dashboard to inspect all past session logs, status results, and interactive tendency charts.

*/
