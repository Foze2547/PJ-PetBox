#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include <Adafruit_VL53L0X.h>

// ============================================
// 1. WiFi Configuration
// ============================================
const char* ssid = "@JumboPlusIoT";
const char* password = "petbox1234";

// ============================================
// 2. HiveMQ Cloud MQTT Configuration
// ============================================
const char* mqtt_server   = "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud";
const int   mqtt_port     = 8883;

// ใส่ username / password จาก HiveMQ Cloud Access Management
const char* mqtt_user     = "Fozexe";
const char* mqtt_password = "MySecurePassword123!";

// MQTT Topics
const char* topic_motor_cmd   = "robot/control/motor";
const char* topic_steer_cmd   = "robot/control/steer";
const char* topic_config_cmd  = "robot/control/config";
const char* topic_status      = "robot/status";
const char* topic_distance    = "robot/sensor/distance";

// ============================================
// 3. Hardware Pins Configuration
// ============================================
// --- DC Motor ---
const int PWM_PIN  = 40;
const int AIN1_PIN = 39;
const int AIN2_PIN = 38;

// --- IR Encoders ---
const int ENCODER_LEFT_PIN  = 14;
const int ENCODER_RIGHT_PIN = 21;

// --- Servos ---
const int SERVO_LEFT_PIN  = 11;
const int SERVO_RIGHT_PIN = 12;

// --- I2C Distance Sensor (VL53L0X) ---
const int I2C_SDA_PIN = 1;
const int I2C_SCL_PIN = 2;
const int OBSTACLE_STOP_MM = 110;

// ============================================
// 4. Global Objects
// ============================================
WiFiClientSecure espClient;
PubSubClient mqttClient(espClient);

Servo servoLeft;
Servo servoRight;
Adafruit_VL53L0X lox = Adafruit_VL53L0X();

// ============================================
// 5. System Variables
// ============================================
// --- Motor Variables ---
int MAX_MOTOR_SPEED = 255;
int currentSpeed = 0;
int targetSpeed = 0;
unsigned long lastStepTime = 0;
int stepInterval = 0;

// --- Servo & Steering ---
const int SERVO_LEFT_HOME  = 85;
const int SERVO_RIGHT_HOME = 100;
int turnAngle = 15;

// --- Distance Sensor ---
unsigned long lastDistanceRead = 0;
bool distanceSensorReady = false;

// --- Steering State Machine ---
enum SteeringState {
  IDLE,
  LEFT_WAIT_IR_RIGHT,
  LEFT_WAIT_IR_LEFT,
  RIGHT_WAIT_IR_LEFT,
  RIGHT_WAIT_IR_RIGHT
};
SteeringState steeringState = IDLE;

// ============================================
// 6. Utility: Publish Status
// ============================================
void publishStatus(const String& msg) {
  Serial.println("[STATUS] " + msg);
  mqttClient.publish(topic_status, msg.c_str(), true);
}

// ============================================
// 7. Hardware Control Functions
// ============================================

// --- Servo Reset ---
void resetServos() {
  servoLeft.write(SERVO_LEFT_HOME);
  servoRight.write(SERVO_RIGHT_HOME);
  steeringState = IDLE;
  publishStatus("servos_reset");
}

// --- Apply Motor Physically ---
void applyMotorSpeed(int speed) {
  if (speed > 0) {
    // Forward (CW)
    digitalWrite(AIN1_PIN, LOW);
    digitalWrite(AIN2_PIN, HIGH);
    ledcWrite(PWM_PIN, speed);
  } 
  else if (speed < 0) {
    // Backward (CCW)
    digitalWrite(AIN1_PIN, HIGH);
    digitalWrite(AIN2_PIN, LOW);
    ledcWrite(PWM_PIN, abs(speed));
  } 
  else {
    // Brake
    digitalWrite(AIN1_PIN, HIGH);
    digitalWrite(AIN2_PIN, HIGH);
    ledcWrite(PWM_PIN, 0);
  }
}

// --- Set Motor Target ---
void setMotorTarget(int speed, int duration_ms) {
  if (speed > MAX_MOTOR_SPEED) speed = MAX_MOTOR_SPEED;
  if (speed < -MAX_MOTOR_SPEED) speed = -MAX_MOTOR_SPEED;

  targetSpeed = speed;
  int diff = abs(targetSpeed - currentSpeed);

  if (diff == 0 || duration_ms == 0) {
    stepInterval = 0;
  } else {
    stepInterval = duration_ms / diff;
    if (stepInterval < 1) stepInterval = 1;
  }
}

// --- Update Motor ---
void updateMotor() {
  if (currentSpeed == targetSpeed) return;

  if (stepInterval == 0) {
    currentSpeed = targetSpeed;
    applyMotorSpeed(currentSpeed);
    return;
  }

  unsigned long now = millis();
  if (now - lastStepTime >= (unsigned long)stepInterval) {
    lastStepTime = now;

    if (currentSpeed < targetSpeed) currentSpeed++;
    else currentSpeed--;

    applyMotorSpeed(currentSpeed);
  }
}

// --- Update Steering ---
void updateSteering() {
  // Steering works only while moving forward
  if (currentSpeed <= 0) {
    if (steeringState != IDLE) resetServos();
    return;
  }

  int irLeft  = digitalRead(ENCODER_LEFT_PIN);
  int irRight = digitalRead(ENCODER_RIGHT_PIN);

  switch (steeringState) {
    case IDLE:
      break;

    case LEFT_WAIT_IR_RIGHT:
      if (irRight == 0) {
        servoRight.write(SERVO_RIGHT_HOME + turnAngle);
        steeringState = LEFT_WAIT_IR_LEFT;
        Serial.print("Left Loop: Servo Right FWD (");
        Serial.print(SERVO_RIGHT_HOME + turnAngle);
        Serial.println(")");
        publishStatus("left_loop_step1");
      }
      break;

    case LEFT_WAIT_IR_LEFT:
      if (irLeft == 0) {
        servoRight.write(SERVO_RIGHT_HOME);
        steeringState = LEFT_WAIT_IR_RIGHT;
        Serial.println("Left Loop: Servo Right HOME");
        publishStatus("left_loop_step2");
      }
      break;

    case RIGHT_WAIT_IR_LEFT:
      if (irLeft == 0) {
        servoLeft.write(SERVO_LEFT_HOME - turnAngle);
        steeringState = RIGHT_WAIT_IR_RIGHT;
        Serial.print("Right Loop: Servo Left FWD (");
        Serial.print(SERVO_LEFT_HOME - turnAngle);
        Serial.println(")");
        publishStatus("right_loop_step1");
      }
      break;

    case RIGHT_WAIT_IR_RIGHT:
      if (irRight == 0) {
        servoLeft.write(SERVO_LEFT_HOME);
        steeringState = RIGHT_WAIT_IR_LEFT;
        Serial.println("Right Loop: Servo Left HOME");
        publishStatus("right_loop_step2");
      }
      break;
  }
}



// --- Read Distance (mm) ---
int readDistanceMM() {
  if (!distanceSensorReady) return -1;

  VL53L0X_RangingMeasurementData_t measure;
  lox.rangingTest(&measure, false);

  if (measure.RangeStatus == 4) return -1;
  return measure.RangeMilliMeter;
}

// --- Update Distance Sensor ---
void updateDistanceSensor() {
  if (!distanceSensorReady) return;
  if (millis() - lastDistanceRead < 200) return;
  lastDistanceRead = millis();

  int distance = readDistanceMM();
  if (distance < 0) return;

  static int lastPublishedDistance = -9999;
  if (abs(distance - lastPublishedDistance) >= 10) {
    String msg = String(distance);
    mqttClient.publish(topic_distance, msg.c_str(), true);
    lastPublishedDistance = distance;
  }

  if (distance > 0 && distance > OBSTACLE_STOP_MM && targetSpeed > 0) {
    setMotorTarget(0, 0);
    resetServos();
    publishStatus("obstacle_detected_stop:" + String(distance) + "mm");
  }
}

// ============================================
// 8. Command Handlers
// ============================================
void handleMotorCommand(const String& cmd) {
  if (cmd == "forward") {
    setMotorTarget(MAX_MOTOR_SPEED, 2000);
    publishStatus("motor_forward");
  }
  else if (cmd == "backward") {
    resetServos();
    setMotorTarget(-MAX_MOTOR_SPEED, 2000);
    publishStatus("motor_backward");
  }
  else if (cmd == "soft_stop") {
    setMotorTarget(0, 1000);
    publishStatus("motor_soft_stop");
  }
  else if (cmd == "hard_stop") {
    setMotorTarget(0, 0);
    resetServos();
    publishStatus("motor_hard_stop");
  }
}

void handleSteerCommand(const String& cmd) {
  // กลับตำแหน่งเริ่มต้นก่อนทุกครั้ง
  servoLeft.write(SERVO_LEFT_HOME);
  servoRight.write(SERVO_RIGHT_HOME);
  steeringState = IDLE;
  delay(200);  // รอให้เซอร์โวกลับถึง Home ก่อน

  if (cmd == "left") {
    if (targetSpeed > 0) {
      steeringState = LEFT_WAIT_IR_RIGHT;
      publishStatus("steer_left_initiated_from_home");
    } else {
      publishStatus("steer_left_ignored_not_forward");
    }
  }
  else if (cmd == "right") {
    if (targetSpeed > 0) {
      steeringState = RIGHT_WAIT_IR_LEFT;
      publishStatus("steer_right_initiated_from_home");
    } else {
      publishStatus("steer_right_ignored_not_forward");
    }
  }
  else if (cmd == "reset") {
    resetServos();
    publishStatus("steer_reset");
  }
}

void handleConfigCommand(const String& cmd) {
  if (cmd.startsWith("speed:")) {
    int val = cmd.substring(6).toInt();
    MAX_MOTOR_SPEED = constrain(val, 0, 255);

    if (abs(targetSpeed) > MAX_MOTOR_SPEED) {
      if (targetSpeed > 0) setMotorTarget(MAX_MOTOR_SPEED, 500);
      else setMotorTarget(-MAX_MOTOR_SPEED, 500);
    }

    publishStatus("speed_set:" + String(MAX_MOTOR_SPEED));
  }
  else if (cmd.startsWith("angle:")) {
    int val = cmd.substring(6).toInt();
    turnAngle = constrain(val, 0, 90);
    publishStatus("angle_set:" + String(turnAngle));
  }
}

// ============================================
// 9. MQTT Callback
// ============================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  String topicStr = String(topic);

  Serial.print("[MQTT] Topic: ");
  Serial.print(topicStr);
  Serial.print(" | Message: ");
  Serial.println(message);

  if (topicStr == topic_motor_cmd) {
    handleMotorCommand(message);
  }
  else if (topicStr == topic_steer_cmd) {
    handleSteerCommand(message);
  }
  else if (topicStr == topic_config_cmd) {
    handleConfigCommand(message);
  }
}

// ============================================
// 10. WiFi Connect
// ============================================
void connectWiFi() {
  Serial.println("\n=================================");
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n=================================");
  Serial.println("WiFi Connected Successfully!");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
  Serial.println("=================================\n");
}

// ============================================
// 11. MQTT Connect
// ============================================
void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Connecting to MQTT... ");

    String clientId = "ESP32-Robot-";
    clientId += String((uint32_t)ESP.getEfuseMac(), HEX);

    if (mqttClient.connect(clientId.c_str(), mqtt_user, mqtt_password)) {
      Serial.println("connected!");
      publishStatus("robot_online");

      mqttClient.subscribe(topic_motor_cmd);
      mqttClient.subscribe(topic_steer_cmd);
      mqttClient.subscribe(topic_config_cmd);

      Serial.println("Subscribed topics:");
      Serial.println(topic_motor_cmd);
      Serial.println(topic_steer_cmd);
      Serial.println(topic_config_cmd);
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retry in 3 seconds...");
      delay(3000);
    }
  }
}

// ============================================
// 12. Setup
// ============================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  // IR Pins
  pinMode(ENCODER_LEFT_PIN, INPUT_PULLUP);
  pinMode(ENCODER_RIGHT_PIN, INPUT_PULLUP);

  // Motor Pins
  pinMode(AIN1_PIN, OUTPUT);
  pinMode(AIN2_PIN, OUTPUT);
  ledcAttach(PWM_PIN, 20000, 8);

  // Servos
  servoLeft.attach(SERVO_LEFT_PIN);
  servoRight.attach(SERVO_RIGHT_PIN);
  resetServos();

  // I2C + VL53L0X
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  if (lox.begin()) {
    distanceSensorReady = true;
    Serial.println("VL53L0X ready");
  } else {
    distanceSensorReady = false;
    Serial.println("VL53L0X not found");
  }

  // WiFi
  connectWiFi();

  // TLS for HiveMQ Cloud
  // สำหรับทดสอบเริ่มต้น ใช้ setInsecure() ก่อน
  // ถ้าจะใช้งานจริงควรใส่ CA certificate
  espClient.setInsecure();

  // MQTT
  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);

  connectMQTT();
}

// ============================================
// 13. Loop
// ============================================
void loop() {
  if (!mqttClient.connected()) {
    connectMQTT();
  }

  mqttClient.loop();
  updateMotor();
  updateSteering();
  updateDistanceSensor();
}
