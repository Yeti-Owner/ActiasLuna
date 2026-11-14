/*
 * Uno Q pin finder — settles whether the on-board white button is readable by the microcontroller.
 *
 * It sets the header pins (D2..D21, A0..A5) as inputs with pull-ups, so each normally reads HIGH, and prints
 * whenever one changes. Press the white button and watch:
 *   - A line like "pin 7 -> LOW (pressed?)" appears when you press  -> the button IS on that pin.
 *     Tell me the number and we'll use it as the crowd button in the Arduino IDE.
 *   - Nothing ever changes no matter how you press  -> the white button is not wired to the microcontroller
 *     (it's on the board's Linux side), so no Arduino IDE sketch can read it. Then an external button on D2 is
 *     the way, or we read the white button from the Linux side instead.
 *
 * Upload, open Serial Monitor at 115200, press and release the white button a few times.
 * (This only touches the header pins; it changes nothing else on the board.)
 */
const int FIRST_PIN = 2;
const int LAST_PIN = 21;   // D2..D13 = 2..13, A0..A5 = 14..19, D20..D21 = 20..21

int lastState[22];

void setup() {
  Serial.begin(115200);
  delay(300);
  for (int p = FIRST_PIN; p <= LAST_PIN; p++) {
    pinMode(p, INPUT_PULLUP);
    lastState[p] = HIGH;
  }
  Serial.println("{\"t\":\"hello\",\"dev\":\"pin_finder\"}");
  Serial.println("Press the white button. If any pin reacts, its number prints here.");
}

void loop() {
  for (int p = FIRST_PIN; p <= LAST_PIN; p++) {
    int now = digitalRead(p);
    if (now != lastState[p]) {
      lastState[p] = now;
      Serial.print("pin ");
      Serial.print(p);
      Serial.print(now == LOW ? " -> LOW (pressed?)" : " -> HIGH (released)");
      Serial.println();
    }
  }
  delay(15);   // light debounce
}
