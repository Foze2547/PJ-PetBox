#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

// =========================
// Wi-Fi
// =========================
const char* WIFI_SSID = "@JumboPlusIoT";
const char* WIFI_PASS = "petbox1234";

// =========================
// HiveMQ Cloud
// =========================
const char* MQTT_HOST = "058acb9373964025a71851d4a0030e8a.s1.eu.hivemq.cloud";
const int MQTT_PORT = 8883;
const char* MQTT_USER = "Fozexe";
const char* MQTT_PASS = "MySecurePassword123!";

// =========================
// Relay pins
// =========================
#define RELAY1_PIN 32
#define RELAY2_PIN 33

const bool RELAY_ACTIVE_LOW = true;

// =========================
// MQTT Topics
// =========================
const char* TOPIC_RELAY1_SET = "mechcode/relay1/set";
const char* TOPIC_RELAY2_SET = "mechcode/relay2/set";
const char* TOPIC_RELAY_ALL_SET = "mechcode/relay/all/set";

const char* TOPIC_RELAY1_STATE = "mechcode/relay1/state";
const char* TOPIC_RELAY2_STATE = "mechcode/relay2/state";
const char* TOPIC_STATUS = "mechcode/esp32/status";

// =========================
// Globals
// =========================
WiFiClientSecure secureClient;
PubSubClient mqttClient(secureClient);

bool relay1State = false;
bool relay2State = false;

// =========================
// Relay control
// =========================
void writeRelay(uint8_t pin, bool on) {
  int level = RELAY_ACTIVE_LOW ? (on ? LOW : HIGH) : (on ? HIGH : LOW);
  digitalWrite(pin, level);

  Serial.print("writeRelay pin=");
  Serial.print(pin);
  Serial.print(" on=");
  Serial.print(on ? "ON" : "OFF");
  Serial.print(" level=");
  Serial.println(level);
}

void applyRelayStates() {
  writeRelay(RELAY1_PIN, relay1State);
  writeRelay(RELAY2_PIN, relay2State);
}

void publishStates() {
  mqttClient.publish(TOPIC_RELAY1_STATE, relay1State ? "ON" : "OFF", true);
  mqttClient.publish(TOPIC_RELAY2_STATE, relay2State ? "ON" : "OFF", true);
}

// =========================
// Wi-Fi
// =========================
void connectWiFi() {
  Serial.print("Connecting WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi connected");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

// =========================
// MQTT callback
// =========================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String msg = "";
  for (unsigned int i = 0; i < length; i++) {
    msg += (char)payload[i];
  }

  msg.trim();
  msg.toUpperCase();

  String topicStr = String(topic);

  Serial.print("Incoming topic: ");
  Serial.print(topicStr);
  Serial.print(" | payload: ");
  Serial.println(msg);

  bool valid = false;
  bool turnOn = false;

  if (msg == "ON" || msg == "1") {
    valid = true;
    turnOn = true;
  } else if (msg == "OFF" || msg == "0") {
    valid = true;
    turnOn = false;
  }

  if (!valid) {
    Serial.println("Invalid command");
    return;
  }

  if (topicStr == TOPIC_RELAY1_SET) {
    relay1State = turnOn;
  } else if (topicStr == TOPIC_RELAY2_SET) {
    relay2State = turnOn;
  } else if (topicStr == TOPIC_RELAY_ALL_SET) {
    relay1State = turnOn;
    relay2State = turnOn;
  } else {
    return;
  }

  applyRelayStates();
  delay(50);
  publishStates();

  Serial.print("Relay1 state = ");
  Serial.println(relay1State ? "ON" : "OFF");
  Serial.print("Relay2 state = ");
  Serial.println(relay2State ? "ON" : "OFF");
}

// =========================
// MQTT connect
// =========================
void connectMQTT() {
  while (!mqttClient.connected()) {
    Serial.print("Connecting MQTT...");

    String clientId = "ESP32-Relay-" + String((uint32_t)ESP.getEfuseMac(), HEX);

    if (mqttClient.connect(
          clientId.c_str(),
          MQTT_USER,
          MQTT_PASS,
          TOPIC_STATUS,
          0,
          true,
          "offline")) {
      Serial.println("connected");

      mqttClient.publish(TOPIC_STATUS, "online", true);

      mqttClient.subscribe(TOPIC_RELAY1_SET);
      mqttClient.subscribe(TOPIC_RELAY2_SET);
      mqttClient.subscribe(TOPIC_RELAY_ALL_SET);

      publishStates();
    } else {
      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());
      Serial.println(" retry in 3 sec");
      delay(3000);
    }
  }
}

// =========================
// Setup
// =========================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(RELAY1_PIN, OUTPUT);
  pinMode(RELAY2_PIN, OUTPUT);

  relay1State = false;
  relay2State = false;
  applyRelayStates();

  connectWiFi();

  // Development only. Replace with CA cert in production.
  secureClient.setInsecure();
  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);

  connectMQTT();

  Serial.println("System ready");
}

// =========================
// Loop
// =========================
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  if (!mqttClient.connected()) {
    connectMQTT();
  }

  mqttClient.loop();
}
