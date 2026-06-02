import tkinter as tk
import os

CALIBRATION_FACTOR = 330  # pulses per liter for Digiten FL-608
GPIO_PIN = 17

pulse_count = 0
total_liters = 0.0

# Set up GPIO — works on Pi with gpiozero, uses mock on everything else
try:
    from gpiozero import Button
    if not os.path.exists('/proc/device-tree/model'):
        from gpiozero.pins.mock import MockFactory
        from gpiozero import Device
        Device.pin_factory = MockFactory()
        print("Running in mock mode (not a Pi)")

    sensor = Button(GPIO_PIN, pull_up=True)

    def pulse_callback():
        global pulse_count
        pulse_count += 1

    sensor.when_pressed = pulse_callback

except Exception as e:
    sensor = None
    print(f"GPIO not available: {e}")


class FlowMonitor(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Flow Monitor")
        self.attributes('-fullscreen', True)
        self.configure(bg='#0a0a0a')
        self.resizable(False, False)

        # Exit on Escape or Q
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<q>', lambda e: self.destroy())
        self.bind('<Q>', lambda e: self.destroy())

        self._build_ui()
        self._update()

    def _build_ui(self):
        # Main centered frame
        frame = tk.Frame(self, bg='#0a0a0a')
        frame.place(relx=0.5, rely=0.5, anchor='center')

        # "FLOW RATE" label
        self.lbl_title = tk.Label(
            frame,
            text='FLOW RATE',
            font=('Courier New', 18, 'normal'),
            fg='#444444',
            bg='#0a0a0a',
            letterSpacing=4,
        )
        self.lbl_title.pack()

        # Big flow number
        self.lbl_flow = tk.Label(
            frame,
            text='0.00',
            font=('Courier New', 120, 'bold'),
            fg='#00e5ff',
            bg='#0a0a0a',
        )
        self.lbl_flow.pack()

        # Unit label
        self.lbl_unit = tk.Label(
            frame,
            text='L / min',
            font=('Courier New', 26, 'normal'),
            fg='#007a8a',
            bg='#0a0a0a',
        )
        self.lbl_unit.pack()

        # Divider
        tk.Frame(frame, bg='#1e1e1e', height=1, width=100).pack(pady=20)

        # Total volume
        self.lbl_total = tk.Label(
            frame,
            text='total:  0.000 L',
            font=('Courier New', 20, 'normal'),
            fg='#333333',
            bg='#0a0a0a',
        )
        self.lbl_total.pack()

        # ESC hint in bottom-right corner
        tk.Label(
            self,
            text='ESC to exit',
            font=('Courier New', 11),
            fg='#1e1e1e',
            bg='#0a0a0a',
        ).place(relx=1.0, rely=1.0, anchor='se', x=-20, y=-16)

    def _update(self):
        global pulse_count, total_liters

        # Grab and reset pulse count atomically
        count = pulse_count
        pulse_count = 0

        flow_lps = count / CALIBRATION_FACTOR       # liters per second
        flow_lpm = flow_lps * 60                     # liters per minute
        total_liters += flow_lps

        self.lbl_flow.config(text=f'{flow_lpm:.2f}')
        self.lbl_total.config(text=f'total:  {total_liters:.3f} L')

        # Flash the number cyan → dim when flow is detected
        if count > 0:
            self.lbl_flow.config(fg='#00e5ff')
        else:
            self.lbl_flow.config(fg='#004d57')

        self.after(1000, self._update)


if __name__ == '__main__':
    app = FlowMonitor()
    app.mainloop()