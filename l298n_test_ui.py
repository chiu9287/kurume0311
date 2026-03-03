import tkinter as tk
from tkinter import Button, Frame, Label

try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False

    class GPIO:
        BCM = "BCM"
        OUT = "OUT"
        HIGH = 1
        LOW = 0

        @staticmethod
        def setmode(mode):
            print(f"[MOCK GPIO] Set mode: {mode}")

        @staticmethod
        def setwarnings(flag):
            pass

        @staticmethod
        def setup(pin, mode):
            print(f"[MOCK GPIO] Setup pin {pin} as {mode}")

        @staticmethod
        def output(pin, state):
            print(f"[MOCK GPIO] Pin {pin} -> {'HIGH' if state else 'LOW'}")

        @staticmethod
        def cleanup():
            print("[MOCK GPIO] Cleanup")


PUMP_IN1 = 6
PUMP_IN2 = 13
VALVE_IN3 = 19
VALVE_IN4 = 26


class L298NTestUI:
    def __init__(self, root):
        self.root = root
        self.root.title("L298N 測試面板")
        self.root.geometry("420x220")

        self.pump_on = False
        self.valve_on = False

        self.init_gpio()
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def init_gpio(self):
        if HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
        else:
            print("[INFO] Running with mock GPIO (Windows development mode)")

        for pin in (PUMP_IN1, PUMP_IN2, VALVE_IN3, VALVE_IN4):
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)

    def build_ui(self):
        container = Frame(self.root, bg="white")
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        self.status_label = Label(container, text="狀態：待命", font=("Arial", 12, "bold"), bg="white")
        self.status_label.pack(pady=(0, 12))

        self.pump_btn = Button(
            container,
            text="打氣馬達：OFF",
            font=("Arial", 12, "bold"),
            bg="#d9534f",
            fg="white",
            height=2,
            command=self.toggle_pump,
        )
        self.pump_btn.pack(fill=tk.X, pady=(0, 10))

        self.valve_btn = Button(
            container,
            text="電磁閥(洩氣)：OFF",
            font=("Arial", 12, "bold"),
            bg="#d9534f",
            fg="white",
            height=2,
            command=self.toggle_valve,
        )
        self.valve_btn.pack(fill=tk.X)

    def toggle_pump(self):
        self.pump_on = not self.pump_on
        if self.pump_on:
            GPIO.output(PUMP_IN1, GPIO.HIGH)
            GPIO.output(PUMP_IN2, GPIO.LOW)
            self.pump_btn.config(text="打氣馬達：ON", bg="#5cb85c")
            self.status_label.config(text="狀態：打氣馬達啟用")
        else:
            GPIO.output(PUMP_IN1, GPIO.LOW)
            GPIO.output(PUMP_IN2, GPIO.LOW)
            self.pump_btn.config(text="打氣馬達：OFF", bg="#d9534f")
            self.status_label.config(text="狀態：打氣馬達關閉")

    def toggle_valve(self):
        self.valve_on = not self.valve_on
        if self.valve_on:
            GPIO.output(VALVE_IN3, GPIO.HIGH)
            GPIO.output(VALVE_IN4, GPIO.LOW)
            self.valve_btn.config(text="電磁閥(洩氣)：ON", bg="#5cb85c")
            self.status_label.config(text="狀態：電磁閥啟用(洩氣)")
        else:
            GPIO.output(VALVE_IN3, GPIO.LOW)
            GPIO.output(VALVE_IN4, GPIO.LOW)
            self.valve_btn.config(text="電磁閥(洩氣)：OFF", bg="#d9534f")
            self.status_label.config(text="狀態：電磁閥關閉")

    def on_close(self):
        for pin in (PUMP_IN1, PUMP_IN2, VALVE_IN3, VALVE_IN4):
            GPIO.output(pin, GPIO.LOW)
        if HAS_GPIO:
            GPIO.cleanup()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = L298NTestUI(root)
    root.mainloop()