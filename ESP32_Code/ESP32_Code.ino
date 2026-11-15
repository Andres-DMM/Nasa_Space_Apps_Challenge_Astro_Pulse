#include <SPI.h>
#include <Wire.h>
#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_GC9A01A.h>
#include <Adafruit_MLX90614.h>
#include "MAX30105.h"
#include "heartRate.h"

// ================= DISPLAY =================
#define TFT_CS   5
#define TFT_DC   4
#define TFT_RST  16
Adafruit_GC9A01A tft(TFT_CS, TFT_DC, TFT_RST);

// ================= I2C SENSORS =================
#define SDA_PIN 21
#define SCL_PIN 22
Adafruit_MLX90614 mlx = Adafruit_MLX90614();
MAX30105 particleSensor;
bool mlxAvailable = false;
bool maxAvailable = false;

// ================= BUTTONS =================
const int tempButtonPin = 27; 
const int bpmButtonPin = 17;
const unsigned long debounceDelay = 50;

struct ButtonState {
  int pin;
  int stableState;
  int lastReading;
  unsigned long lastDebounceTime;
};
ButtonState tempButton = {tempButtonPin, HIGH, HIGH, 0};
ButtonState bpmButton  = {bpmButtonPin, HIGH, HIGH, 0};

// ================= MODES =================
enum MonitorMode { MODE_IDLE, MODE_TEMP, MODE_BPM };
MonitorMode activeMode = MODE_IDLE;
bool transmitting = false;

// ================= TIMING =================
unsigned long lastSendTime = 0;
const unsigned long sendInterval = 1000;

// ================= BPM CALCULATION =================
const byte RATE_SIZE = 4;
byte rates[RATE_SIZE] = {0};
byte rateSpot = 0;
long lastBeat = 0;
float currentBPM = 0;
int averageBPM = 0;
bool fingerPresent = false;

// ================= BUTTON DEBOUNCE =================
bool wasPressed(ButtonState &button) {
  if (millis() < 2000) return false; // 2-second startup lockout

  int reading = digitalRead(button.pin);
  if (reading != button.lastReading) {
    button.lastDebounceTime = millis();
  }
  if ((millis() - button.lastDebounceTime) > debounceDelay) {
    if (reading != button.stableState) {
      button.stableState = reading;
      if (button.stableState == LOW) {
        button.lastReading = reading;
        return true;
      }
    }
  }
  button.lastReading = reading;
  return false;
}

void resetBPMValues() {
  for (byte i = 0; i < RATE_SIZE; i++) rates[i] = 0;
  rateSpot = 0;
  lastBeat = 0;
  currentBPM = 0;
  averageBPM = 0;
  fingerPresent = false;
}

// ================= DISPLAY =================
void drawFrame(const char *heading) {
  tft.fillScreen(GC9A01A_BLACK);
  tft.drawCircle(120, 120, 115, GC9A01A_CYAN);
  tft.setTextColor(GC9A01A_WHITE);
  tft.setTextSize(2);
  tft.setCursor(45, 28);
  tft.println(heading);
  tft.drawFastHLine(45, 125, 150, GC9A01A_WHITE);
}

void drawIdleScreen() {
  drawFrame("VITAL MONITOR");
  tft.setTextColor(GC9A01A_YELLOW);
  tft.setTextSize(2);
  tft.setCursor(43, 90);
  tft.println("READY");
  tft.setTextSize(1);
  tft.setTextColor(GC9A01A_WHITE);
  tft.setCursor(35, 145);
  tft.println("TEMP: BUTTON 27");
  tft.setCursor(35, 160);
  tft.println("BPM: BUTTON 17");
}

void drawTempDashboard() {
  drawFrame("TEMP MONITOR");
  tft.setTextColor(GC9A01A_YELLOW);
  tft.setCursor(75, 65);
  tft.println("AMBIENT");
  tft.setTextColor(GC9A01A_CYAN);
  tft.setCursor(83, 140);
  tft.println("OBJECT");
  updateStatus();
}

void drawBPMDashboard() {
  drawFrame("HEART RATE");
  // Removed "AVERAGE" text per request; only showing BPM number and label
  tft.setTextColor(GC9A01A_YELLOW);
  tft.setTextSize(2);
  tft.setCursor(100, 145);
  tft.println("BPM");
  updateStatus();
}

void updateStatus() {
  tft.fillRect(35, 205, 170, 20, GC9A01A_BLACK);
  tft.setTextSize(2);
  if (transmitting) {
    tft.setTextColor(GC9A01A_GREEN);
    tft.setCursor(77, 207);
    tft.print("STREAM ON");
  } else {
    tft.setTextColor(GC9A01A_RED);
    tft.setCursor(77, 207);
    tft.print("STREAM OFF");
  }
}

void updateTemperature(int ambient, int objectTemp) {
  char ambientText[16], objectText[16];
  snprintf(ambientText, sizeof(ambientText), "%d C", ambient);
  snprintf(objectText, sizeof(objectText), "%d C", objectTemp);
  tft.fillRect(45, 92, 150, 30, GC9A01A_BLACK);
  tft.setTextColor(GC9A01A_YELLOW);
  tft.setTextSize(3);
  tft.setCursor(65, 95);
  tft.print(ambientText);
  tft.fillRect(45, 155, 150, 35, GC9A01A_BLACK);
  tft.setTextColor(GC9A01A_CYAN);
  tft.setCursor(65, 157);
  tft.print(objectText);
}

void updateBPM(int bpm) {
  tft.fillRect(45, 98, 150, 35, GC9A01A_BLACK);
  tft.setTextSize(3);
  tft.setTextColor(GC9A01A_YELLOW);
  tft.setCursor(75, 100);
  if (bpm <= 0) {
    tft.setTextSize(2);
    tft.setCursor(60, 105);
    tft.print("NO FINGER");
  } else {
    tft.printf("%d", bpm);
  }
}

void showSummary(JsonObject summary) {
  const char *modeText = summary["mode"] | "TEMP";
  bool isBPM = (strcmp(modeText, "BPM") == 0);
  tft.fillScreen(GC9A01A_BLACK);
  tft.drawCircle(120, 120, 115, GC9A01A_CYAN);
  tft.setTextColor(GC9A01A_WHITE);
  tft.setTextSize(2);
  tft.setCursor(50, 15);
  tft.print(isBPM ? "BPM SUMMARY" : "TEMP SUMMARY");
  
  const char *unit = isBPM ? " BPM" : " C";
  tft.setTextColor(GC9A01A_YELLOW);
  tft.setCursor(25, 45);
  tft.printf("AVG: %.1f%s", summary["average"].as<float>(), unit);
  
  // Display threshold status / medical statement on screen
  const char *statusMsg = summary["status_message"] | "";
  tft.setTextSize(1);
  tft.setTextColor(GC9A01A_GREEN);
  tft.setCursor(25, 80);
  tft.println(statusMsg);

  tft.setTextColor(GC9A01A_CYAN);
  tft.setCursor(25, 110);
  tft.printf("MIN: %.1f%s", summary["minimum"].as<float>(), unit);
  tft.setCursor(25, 130);
  tft.printf("MAX: %.1f%s", summary["maximum"].as<float>(), unit);

  tft.setTextColor(GC9A01A_WHITE);
  tft.setCursor(25, 160);
  tft.printf("Samples: %d", summary["sample_count"].as<int>());

  tft.setTextSize(1);
  tft.setCursor(25, 195);
  tft.setTextColor(GC9A01A_RED);
  tft.print(isBPM ? "HOBBY SENSOR - NOT MEDICAL" : "SURFACE TEMP - NOT MEDICAL");
}

// ================= SERIAL RECEIVE =================
void checkSerialCommands() {
  static String incomingLine = "";
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      incomingLine.trim();
      if (incomingLine.startsWith("SUMMARY,")) {
        String json = incomingLine.substring(8);
        DynamicJsonDocument doc(1024);
        DeserializationError error = deserializeJson(doc, json);
        if (!error && !doc["summary"].isNull() && !transmitting) {
          showSummary(doc["summary"].as<JsonObject>());
        }
      }
      incomingLine = "";
    } else if (c != '\r' && incomingLine.length() < 1500) {
      incomingLine += c;
    }
  }
}

// ================= MODE CONTROL =================
void stopStreaming() {
  if (!transmitting) return;
  transmitting = false;
  activeMode = MODE_IDLE;
  Serial.println("STREAM,OFF");
  drawIdleScreen();
}

void startMode(MonitorMode selectedMode) {
  if (transmitting && activeMode == selectedMode) {
    stopStreaming();
    return;
  }

  if (selectedMode == MODE_TEMP && !mlxAvailable) {
    tft.fillScreen(GC9A01A_BLACK);
    tft.setTextColor(GC9A01A_RED);
    tft.setTextSize(2);
    tft.setCursor(35, 90);
    tft.println("MLX90614 ERROR");
    tft.setTextSize(1);
    tft.setCursor(25, 120);
    tft.println("Check I2C wiring");
    return;
  }

  if (selectedMode == MODE_BPM && !maxAvailable) {
    tft.fillScreen(GC9A01A_BLACK);
    tft.setTextColor(GC9A01A_RED);
    tft.setTextSize(2);
    tft.setCursor(25, 90);
    tft.println("MAX30102 ERROR");
    tft.setTextSize(1);
    tft.setCursor(25, 120);
    tft.println("Check I2C wiring");
    return;
  }

  if (transmitting) stopStreaming();

  activeMode = selectedMode;
  transmitting = true;
  lastSendTime = millis();

  if (activeMode == MODE_TEMP) {
    drawTempDashboard();
    Serial.println("STREAM,TEMP,ON");
  } else {
    resetBPMValues();
    drawBPMDashboard();
    Serial.println("STREAM,BPM,ON");
  }
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);
  delay(500);

  tft.begin();
  tft.setRotation(0);

  pinMode(tempButtonPin, INPUT_PULLUP);
  pinMode(bpmButtonPin, INPUT_PULLUP);

  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  delay(100);

  mlxAvailable = mlx.begin();
  maxAvailable = particleSensor.begin(Wire, I2C_SPEED_STANDARD);

  if (maxAvailable) {
    particleSensor.setup();
    particleSensor.setPulseAmplitudeRed(0x0A);
    particleSensor.setPulseAmplitudeGreen(0);
  }

  transmitting = false;
  activeMode = MODE_IDLE;
  drawIdleScreen();

  Serial.println("ESP32_READY");
  Serial.printf("MLX90614,%s\n", mlxAvailable ? "OK" : "ERROR");
  Serial.printf("MAX30102,%s\n", maxAvailable ? "OK" : "ERROR");
}

// ================= MAIN LOOP =================
void loop() {
  if (wasPressed(tempButton)) startMode(MODE_TEMP);
  if (wasPressed(bpmButton)) startMode(MODE_BPM);
  checkSerialCommands();

  if (transmitting && activeMode == MODE_TEMP && millis() - lastSendTime >= sendInterval) {
    lastSendTime = millis();
    float ambF = mlx.readAmbientTempC();
    float objF = mlx.readObjectTempC();

    if (!isnan(ambF) && !isnan(objF) && ambF > -40 && objF < 150) {
      int ambientTemp = (int)round(ambF);
      int objectTemp = (int)round(objF);
      updateTemperature(ambientTemp, objectTemp);
      Serial.printf("TEMP,%d,%d\n", ambientTemp, objectTemp);
    }
  }

  if (transmitting && activeMode == MODE_BPM) {
    long irValue = particleSensor.getIR();

    if (irValue < 5000) {
      fingerPresent = false;
      resetBPMValues();
    } else {
      fingerPresent = true;
      if (checkForBeat(irValue) == true) {
        long now = millis();
        long delta = now - lastBeat;
        lastBeat = now;
        currentBPM = 60.0 / (delta / 1000.0);
        if (currentBPM < 255 && currentBPM > 20) {
          rates[rateSpot++] = (byte)currentBPM;
          rateSpot %= RATE_SIZE;
          int sum = 0;
          for (byte x = 0; x < RATE_SIZE; x++) sum += rates[x];
          averageBPM = sum / RATE_SIZE;
        }
      }
    }

    if (millis() - lastSendTime >= sendInterval) {
      lastSendTime = millis();
      if (!fingerPresent || averageBPM == 0) {
        updateBPM(0);
        Serial.println("BPM,0");
      } else {
        updateBPM(averageBPM);
        Serial.printf("BPM,%d\n", averageBPM);
      }
    }
  }
}