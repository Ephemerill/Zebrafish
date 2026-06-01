import time
from gpiozero import Button

CALIBRATION_FACTOR = 330
pulse_count = 0
total_liters = 0.0

sensor = Button(17, pull_up=True)

def pulse_callback():
    global pulse_count
    pulse_count += 1

sensor.when_pressed = pulse_callback

try:
    print("Monitoring water flow... Press Ctrl+C to stop.")
    while True:
        pulse_count = 0
        time.sleep(1)

        flow_rate = pulse_count / CALIBRATION_FACTOR
        flow_lpm  = flow_rate * 60
        total_liters += flow_rate

        print(f"Flow: {flow_lpm:.2f} L/min | Total: {total_liters:.3f} L")

except KeyboardInterrupt:
    print("Stopped.")