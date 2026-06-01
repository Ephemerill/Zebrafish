import RPi.GPIO as GPIO
import time

FLOW_SENSOR_PIN = 17          # BCM numbering (physical pin 11)
CALIBRATION_FACTOR = 7.5      # Pulses per liter — adjust for your model

pulse_count = 0
total_liters = 0.0

GPIO.setmode(GPIO.BCM)
GPIO.setup(FLOW_SENSOR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def pulse_callback(channel):
    global pulse_count
    pulse_count += 1

GPIO.add_event_detect(FLOW_SENSOR_PIN, GPIO.FALLING, callback=pulse_callback)

try:
    print("Monitoring water flow... Press Ctrl+C to stop.")
    while True:
        pulse_count = 0
        time.sleep(1)  # Sample every second

        flow_rate = (pulse_count / CALIBRATION_FACTOR)  # Liters per second
        flow_lpm  = flow_rate * 60                       # Liters per minute
        total_liters += flow_rate

        print(f"Flow: {flow_lpm:.2f} L/min | Total: {total_liters:.3f} L")

except KeyboardInterrupt:
    print("Stopped.")
    GPIO.cleanup()