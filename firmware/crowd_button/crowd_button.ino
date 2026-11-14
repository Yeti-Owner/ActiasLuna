/*
 * Moth crowd button (Arduino / Uno Q MCU side)
 *
 * A single button toggles the SIMULATED decoy crowd on the dashboard. The crowd is a display effect only:
 * it is drawn as clearly-labelled fake phones and nothing is ever transmitted over Wi-Fi.
 *
 * On each press it sends one line over USB serial:
 *     {"t":"crowd","on":1}\n   or   {"t":"crowd","on":0}\n
 * tools/unoq_crowd_relay.py reads those lines and forwards them to the Moth server as UDP.
 *
 * No button yet? You can also toggle by typing any letter + Enter in the Serial Monitor
 * (see the serial-command block in loop()). Handy for testing and as a backup during the demo.
 *
 * Wiring: a momentary button from BUTTON_PIN to GND (the internal pull-up is used, so no resistor needed).
 * Many boards have a usable on-board button; set BUTTON_PIN to it if so.
 */

const int BUTTON_PIN = 2;          // button to GND; change to your board's button pin
const unsigned long DEBOUNCE_MS = 40;
const bool DEBUG = true;            // set false once the button works to stop the heartbeat prints

bool crowdOn = false;
int lastReading = HIGH;
int stableState = HIGH;
unsigned long lastChange = 0;
unsigned long lastBeat = 0;

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);   // pressed = LOW
  delay(300);                          // let USB settle (don't block forever waiting on Serial)
  Serial.println("{\"t\":\"hello\",\"dev\":\"crowd_button\"}");
}

void emitCrowd() {
  Serial.print("{\"t\":\"crowd\",\"on\":");
  Serial.print(crowdOn ? 1 : 0);
  Serial.println("}");
}

void loop() {
  int reading = digitalRead(BUTTON_PIN);

  // DEBUG heartbeat: prints the raw pin level twice a second.
  // At rest it should read raw=1; touch D2 to GND and it should drop to raw=0.
  if (DEBUG && millis() - lastBeat > 500) {
    lastBeat = millis();
    Serial.print("raw=");
    Serial.println(reading);
  }

  // Serial-command toggle: type any letter + Enter in the Serial Monitor to toggle the
  // crowd with no button. Whitespace is ignored so stray newlines don't trigger it.
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch != '\r' && ch != '\n' && ch != ' ' && ch != '\t') {
      crowdOn = !crowdOn;
      emitCrowd();
    }
  }

  // Button: a debounced press (D2 pulled to GND) toggles the crowd.
  if (reading != lastReading) {
    lastChange = millis();
    lastReading = reading;
  }
  if (millis() - lastChange > DEBOUNCE_MS && reading != stableState) {
    stableState = reading;
    if (stableState == LOW) {          // a fresh press
      crowdOn = !crowdOn;
      emitCrowd();
    }
  }
}
