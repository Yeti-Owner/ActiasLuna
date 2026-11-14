/*
 * Moth live MAC randomizer (ESP32 / ESP32-S3) — honest "disappear from tracking" demo.
 *
 * ONE real device joins the demo hotspot and changes ITS OWN Wi-Fi address every ROTATE_S seconds,
 * exactly like a phone's "Private Wi-Fi Address" (iOS) / "Randomized MAC" (Android). Moth's receivers
 * hear what looks like a brand-new device each time, so the person's trail keeps resetting and can't be
 * followed over time — a live, over-the-air demonstration of the privacy defense the simulation shows on screen.
 *
 * This stays on the right side of the line:
 *   - It uses its OWN randomized, locally-administered address. It never impersonates another real device
 *     and never forges a crowd of fake devices. It's self-randomization, the same thing every modern phone does.
 *   - It generates only light, meaningless traffic so the receivers have frames to measure.
 *
 * Hardware: a spare ESP32 / ESP32-S3 acting as the "demo phone" (NOT the Uno Q — its Wi-Fi is on the Linux side;
 * see docs/CROWD.md for the Uno Q alternative). Needs the "esp32 by Espressif" board package in the Arduino IDE.
 *
 * Fill in the hotspot name/password below to match config/network.json. Do not commit real secrets.
 */
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_wifi.h"

const char *SSID = "DEMO-DONT JOIN";
const char *PASS = "<hotspot password>";   // from config/network.json; leave the placeholder out of git
const uint32_t ROTATE_S = 8;               // change our own address every N seconds
const char *SERVER_IP = "192.168.4.2";     // laptop IP, just to aim the light traffic somewhere
const uint16_t SERVER_PORT = 5005;
const bool DEBUG = true;

WiFiUDP udp;
uint32_t lastRotate = 0;
uint32_t lastBeat = 0;
int identities = 0;

// Give ourselves a fresh, random, locally-administered unicast MAC and rejoin with it.
void newIdentity() {
  uint8_t mac[6];
  esp_fill_random(mac, sizeof(mac));
  mac[0] = (mac[0] & 0xFE) | 0x02;   // clear multicast bit, set "locally administered" bit

  WiFi.disconnect(true, false);
  delay(100);
  esp_wifi_set_mac(WIFI_IF_STA, mac);
  WiFi.begin(SSID, PASS);
  identities++;

  if (DEBUG) {
    char line[80];
    snprintf(line, sizeof(line),
             "{\"t\":\"macrot\",\"n\":%d,\"mac\":\"%02X:%02X:%02X:%02X:%02X:%02X\"}",
             identities, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    Serial.println(line);
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  delay(200);
  Serial.println("{\"t\":\"hello\",\"dev\":\"mac_randomizer\"}");
  newIdentity();
  lastRotate = millis();
}

void loop() {
  uint32_t now = millis();

  if (now - lastRotate >= ROTATE_S * 1000UL) {
    lastRotate = now;
    newIdentity();
  }

  // Light traffic so the Moth boards have frames (from the current MAC) to measure.
  if (WiFi.status() == WL_CONNECTED && now - lastBeat >= 200) {
    lastBeat = now;
    udp.beginPacket(SERVER_IP, SERVER_PORT);
    udp.print("moth-demo-phone");
    udp.endPacket();
  }
}
