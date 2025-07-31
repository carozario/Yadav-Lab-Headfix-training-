#include "HX711.h"  // this library handles the load cell for weight measurement

// define pins for all the hardware components
#define LOADCELL_DOUT_PIN 3
#define LOADCELL_SCK_PIN 2
#define REWARD_SOLENOID_PIN 7
#define PISTON_SOLENOID_PIN 8
#define SWITCH_LEFT_PIN 5
#define SWITCH_RIGHT_PIN 4
#define CAP_SENSOR_PIN 6
#define FORWARD_PIN 11
#define BACKWARD_PIN 12
#define UPWARD_PIN 9
#define DOWNWARD_PIN 13

HX711 loadcell;
float calibration_factor = -3060.5;  // calibration factor for the load cell, found experimentally

// timing variables
unsigned long tStart;             // when the session started
unsigned long lastWeightTime = 0; // used to limit how often weight is sent over serial
unsigned long lastRewardTime = 0; // tracks the last time water reward was given

// parameters that can be changed from the GUI
unsigned long rewardDelay = 1000;     // delay between rewards in ms
unsigned long rewardDuration = 65;    // how long to open the solenoid for reward
unsigned long rewardBuffer = 1000;    // how long fixation must last before reward is allowed

// state variables
bool fixationActive = false;
bool flushing = false;
bool allow_free_reward = true;
bool habituationMode = false;
bool sessionActive = false;

// fixation-related parameters
int struggleThreshold = 250;      // load cell value at which we consider it a struggle
unsigned long fixDuration = 5000; // how long fixation should last before auto-release
unsigned long fixationStartTime = 0;
unsigned long lastFixEndTime = 0; // when the last fixation ended

unsigned long fixDelay = 2000; // minimum time between fixations
unsigned long fixBuffer = 500; // if fixation ends before this, it's counted as an "escape"
bool timeupCooldown = false;

// trial counters
int trialFix = 0;
int trialEscape = 0;
int trialTimeup = 0;
int trialStruggle = 0;
int trialReward = 0;

// actuator level control
int currentLevel = 1;
const int TOTAL_TRAVEL_TIME = 5500;  // time it takes to go from fully extended to fully retracted
int stepTime = TOTAL_TRAVEL_TIME / 4; // time for each level step

int consecutiveRewards = 0; // used for habituation mode (25 rewards → actuator moves back)


// sends trial data to the python GUI
void sendEvent(unsigned long durationSec) {
  if (!sessionActive) return; // only send events when session is active
  Serial.print("EVENT,");
  Serial.print(durationSec); Serial.print(",");
  Serial.print(trialFix); Serial.print(",");
  Serial.print(trialEscape); Serial.print(",");
  Serial.print(trialTimeup); Serial.print(",");
  Serial.print(trialStruggle); Serial.print(",");
  Serial.println(trialReward);
}

// resets counters for each new trial
void resetTrialCounters() {
  trialFix = 0;
  trialEscape = 0;
  trialTimeup = 0;
  trialStruggle = 0;
  trialReward = 0;
}

// stops actuator movement
void stopSpout() {
  digitalWrite(FORWARD_PIN, LOW);
  digitalWrite(BACKWARD_PIN, LOW);
  digitalWrite(UPWARD_PIN, LOW);
  digitalWrite(DOWNWARD_PIN, LOW);
}

// moves actuator to fully extended position at startup
void homeActuator() {
  stopSpout();
  digitalWrite(FORWARD_PIN, HIGH);   // move forward until fully extended
  delay(TOTAL_TRAVEL_TIME + 1000);   // wait for it to reach the end
  stopSpout();
  currentLevel = 1;
  Serial.println("Actuator Homed to Level 1 (Fully Extended)");
}

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(100);

  // configure pins
  pinMode(REWARD_SOLENOID_PIN, OUTPUT);
  pinMode(PISTON_SOLENOID_PIN, OUTPUT);
  digitalWrite(REWARD_SOLENOID_PIN, HIGH);
  digitalWrite(PISTON_SOLENOID_PIN, HIGH);

  pinMode(SWITCH_LEFT_PIN, INPUT_PULLUP);
  pinMode(SWITCH_RIGHT_PIN, INPUT_PULLUP);
  pinMode(CAP_SENSOR_PIN, INPUT);

  pinMode(FORWARD_PIN, OUTPUT);
  pinMode(BACKWARD_PIN, OUTPUT);
  pinMode(UPWARD_PIN, OUTPUT);
  pinMode(DOWNWARD_PIN, OUTPUT);
  stopSpout();

  // set up load cell
  loadcell.begin(LOADCELL_DOUT_PIN, LOADCELL_SCK_PIN);
  loadcell.set_scale(calibration_factor);
  loadcell.tare();

  Serial.println("System Booting...");
  delay(1000);
  homeActuator(); // fully extend actuator at startup

  // this prevents the very first fixation from skipping the fixDelay
  lastFixEndTime = millis();

  Serial.println("System Ready");
  tStart = millis();
}

void loop() {
  float wt = loadcell.get_units(); // get weight reading

  // send weight data every 100 ms
  if (millis() - lastWeightTime > 100) {
    Serial.print("W,");
    Serial.print(wt);
    Serial.print(",");
    Serial.println(millis() - tStart);
    lastWeightTime = millis();
  }

  // read lever states
  int swL = digitalRead(SWITCH_LEFT_PIN);
  int swR = digitalRead(SWITCH_RIGHT_PIN);
  bool leftPressed = swL == HIGH;
  bool rightPressed = swR == HIGH;

  if (!leftPressed && !rightPressed) timeupCooldown = false;

  // if both levers are pressed, no fixation is active, and fixDelay has passed → engage fixation
  if (leftPressed && rightPressed && !fixationActive && !timeupCooldown &&
      millis() - lastFixEndTime >= fixDelay) {
    fixationActive = true;
    digitalWrite(PISTON_SOLENOID_PIN, LOW); // engage piston
    fixationStartTime = millis();
    Serial.println("Fixation Engaged");
    resetTrialCounters();
    trialFix = 1;
  }

  // manual release (levers released early)
  if ((!leftPressed || !rightPressed) && fixationActive) {
    fixationActive = false;
    digitalWrite(PISTON_SOLENOID_PIN, HIGH); // release piston
    lastFixEndTime = millis(); // update last fixation end time

    unsigned long durationSec = (millis() - fixationStartTime) / 1000.0;

    // check if this counts as an escape
    if (millis() - fixationStartTime < fixBuffer) {
      Serial.println("Escape Event");
      trialEscape = 1;
    } else {
      Serial.println("Fixation Released");
    }

    sendEvent(durationSec);
    resetTrialCounters();
  }

  // auto-release if fixation lasts longer than fixDuration
  if (fixationActive && (millis() - fixationStartTime >= fixDuration)) {
    fixationActive = false;
    digitalWrite(PISTON_SOLENOID_PIN, HIGH);
    lastFixEndTime = millis();

    trialTimeup = 1;
    unsigned long durationSec = (millis() - fixationStartTime) / 1000.0;

    Serial.println("Time-Up Release");
    sendEvent(durationSec);
    resetTrialCounters();
    timeupCooldown = true;
  }

  // release fixation if rat struggles (weight exceeds threshold)
  if (fixationActive && abs(wt) > struggleThreshold) {
    fixationActive = false;
    digitalWrite(PISTON_SOLENOID_PIN, HIGH);
    lastFixEndTime = millis();

    trialStruggle = 1;
    unsigned long durationSec = (millis() - fixationStartTime) / 1000.0;

    Serial.println("Fixation Released due to struggle");
    Serial.println("Struggle YES");
    sendEvent(durationSec);
    resetTrialCounters();
  } 
  else if (fixationActive) {
    Serial.println("Struggle NO"); // prints regularly while fixation is active
  }

  // reward logic
  bool rewardAllowed = allow_free_reward ||
                       (fixationActive && millis() - fixationStartTime >= rewardBuffer);

  if (digitalRead(CAP_SENSOR_PIN) == HIGH &&   // rat licks
      millis() - lastRewardTime > rewardDelay && // enough time has passed
      rewardAllowed && 
      !flushing) {

    giveWater();
    lastRewardTime = millis();
    trialReward++;

    // if free rewards are allowed, count them even when not in fixation
    if (!fixationActive && sessionActive) {
      sendEvent(0);
      resetTrialCounters();
    }
  }

  if (flushing) digitalWrite(REWARD_SOLENOID_PIN, LOW); // keep water on during flush mode

  // read serial commands from GUI
  if (Serial.available()) {
    char cmd = Serial.read();
    switch (cmd) {
      case 'j': digitalWrite(PISTON_SOLENOID_PIN, HIGH); fixationActive = false; Serial.println("Emergency Release"); break;
      case 'b': sessionActive = true; tStart = millis(); Serial.println("Session Started"); break;
      case 'c': sessionActive = false; Serial.println("Session Stopped"); break;
      case 'w': toggleFlush(); break;
      case 'F': stopSpout(); digitalWrite(BACKWARD_PIN, HIGH); break;
      case 'B': stopSpout(); digitalWrite(FORWARD_PIN, HIGH); break;
      case 'U': stopSpout(); digitalWrite(UPWARD_PIN, HIGH); break;
      case 'D': stopSpout(); digitalWrite(DOWNWARD_PIN, HIGH); break;
      case 'S': stopSpout(); break;

      // the next cases parse numbers sent from python
      case 'R': { String num = Serial.readStringUntil('\n'); rewardDuration = num.toInt(); break; }
      case 'M': { while (!Serial.available()); char val = Serial.read(); allow_free_reward = (val == '1'); break; }
      case 'H': { while (!Serial.available()); char val = Serial.read(); habituationMode = (val == '1'); Serial.println(habituationMode ? "Habituation ON" : "Habituation OFF"); break; }
      case 'T': { String num = Serial.readStringUntil('\n'); struggleThreshold = num.toInt(); break; }
      case 'X': { String num = Serial.readStringUntil('\n'); fixDuration = num.toInt(); break; }
      case 'Y': { String num = Serial.readStringUntil('\n'); fixDelay = num.toInt(); break; }
      case 'Z': { String num = Serial.readStringUntil('\n'); fixBuffer = num.toInt(); break; }
      case 'Q': { String num = Serial.readStringUntil('\n'); rewardBuffer = num.toInt(); break; }

      // actuator level control (levels 1–5)
      case 'L': {
        int newLevel = Serial.parseInt();
        if (newLevel >= 1 && newLevel <= 5 && newLevel != currentLevel) {
          int diff = newLevel - currentLevel;
          int moveTime = abs(diff) * stepTime;
          stopSpout();
          if (diff > 0) digitalWrite(BACKWARD_PIN, HIGH); // move actuator backward
          else digitalWrite(FORWARD_PIN, HIGH);          // move actuator forward
          delay(moveTime);
          stopSpout();
          currentLevel = newLevel;
          Serial.print("Moved to Level ");
          Serial.println(currentLevel);
        }
        break;
      }
    }
    while (Serial.available()) Serial.read(); // clear out any leftover serial data
  }
}

// function that actually opens solenoid for reward
void giveWater() {
  Serial.println("Reward Given");
  digitalWrite(REWARD_SOLENOID_PIN, LOW);
  delay(rewardDuration);   // keep solenoid open
  digitalWrite(REWARD_SOLENOID_PIN, HIGH);

  // habituation mode logic (moves actuator back after 25 rewards)
  if (habituationMode) {
    consecutiveRewards++;
    if (consecutiveRewards >= 25 && currentLevel > 1) {
      currentLevel--;
      stopSpout();
      digitalWrite(FORWARD_PIN, HIGH); // move actuator forward (extend)
      delay(stepTime);
      stopSpout();
      Serial.print("Habituation moved to Level ");
      Serial.println(currentLevel);
      consecutiveRewards = 0;
    }
  } else {
    consecutiveRewards = 0;
  }
}

// toggles flush water mode on/off
void toggleFlush() {
  flushing = !flushing;
  digitalWrite(REWARD_SOLENOID_PIN, flushing ? LOW : HIGH);
  Serial.println(flushing ? "Flush ON" : "Flush OFF");
}
