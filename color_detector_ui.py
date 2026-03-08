import cv2
import numpy as np
import tkinter as tk
from tkinter import Label, Frame, Scale, Button, Canvas, HORIZONTAL
from PIL import Image, ImageTk
import threading
import time
import platform

# Conditional GPIO import - use mock on Windows, real on Raspberry Pi
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    # Mock GPIO for Windows development
    HAS_GPIO = False
    class GPIO:
        BCM = 'BCM'
        OUT = 'OUT'
        IN = 'IN'
        PUD_DOWN = 'PUD_DOWN'
        HIGH = 1
        LOW = 0
        
        @staticmethod
        def setmode(mode):
            print(f"[MOCK GPIO] Set mode: {mode}")
        
        @staticmethod
        def setwarnings(flag):
            pass
        
        @staticmethod
        def setup(pin, mode, pull_up_down=None):
            if pull_up_down:
                print(f"[MOCK GPIO] Setup pin {pin} as {mode}, pud={pull_up_down}")
            else:
                print(f"[MOCK GPIO] Setup pin {pin} as {mode}")
        
        @staticmethod
        def output(pin, state):
            print(f"[MOCK GPIO] Pin {pin} -> {'HIGH' if state else 'LOW'}")

        @staticmethod
        def input(pin):
            # Default LOW in mock mode; external trigger can be simulated in code path.
            return GPIO.LOW
        
        @staticmethod
        def cleanup():
            print("[MOCK GPIO] Cleanup")

# GPIO Pin Configuration (Raspberry Pi 5 BCM)
GRIP_PINS = {
    'red_big': 17,      # RED BIG circle grip
    'red_small': 18,    # RED SMALL circle grip
    'blue_big': 22,     # BLUE BIG circle grip
    'blue_small': 23    # BLUE SMALL circle grip
}
SIGNAL_PIN = 24         # Send/Receive signal (HIGH/LOW)
PUMP_FWD = 6          # L298N IN1: Pump motor forward (inflate)
PUMP_BWD = 13          # L298N IN2: Pump motor backward (deflate)
VALVE_FWD = 19          # L298N IN1: Pump motor forward (inflate)
VALVE_BWD = 26          # L298N IN2: Pump motor backward (deflate)

# Position control pins
CIRCLE_BIT0 = 12        # Circle type encoding bit 0 (圓圈種類)
CIRCLE_BIT1 = 16        # Circle type encoding bit 1 (圓圈種類)
POSITION_TRIGGER = 20   # Trigger signal for position movement
STATE_TRIGGER = 21      # State trigger signal
READY_INPUT_PIN = 4     # Input signal from external controller

CIRCLE_CODE_MAP = {
    ('red', 'big'): (0, 0),
    ('red', 'small'): (0, 1),
    ('blue', 'big'): (1, 0),
    ('blue', 'small'): (1, 1),
}

class DualSlider(Frame):
    def __init__(self, parent, min_val, max_val, length, command=None, bg='white'):
        super().__init__(parent, bg=bg)
        self.min_val = min_val
        self.max_val = max_val
        self.command = command or (lambda: None)
        self.length = length
        self.canvas = Canvas(self, width=length, height=20, bg=self['bg'], highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, expand=True, fill=tk.X)
        # draw track: full gray bar + black selection
        self.track_y1 = 8
        self.track_y2 = 12
        self.track = self.canvas.create_rectangle(0, self.track_y1, length, self.track_y2, fill='#bbbbbb', outline='')
        self.selection = self.canvas.create_rectangle(0, self.track_y1, 0, self.track_y2, fill='black', outline='')
        # handles are small rectangles
        self.low_handle = self.canvas.create_rectangle(0, 0, 10, 20, fill='black')
        self.high_handle = self.canvas.create_rectangle(length-10, 0, length, 20, fill='black')
        self.active_handle = None
        self.low = min_val
        self.high = max_val
        self.canvas.bind('<Button-1>', self.on_click)
        self.canvas.bind('<B1-Motion>', self.on_drag)
        # update track width when canvas resized
        self.canvas.bind('<Configure>', self._on_resize)
    
    def on_click(self, event):
        x = event.x
        lx = self.canvas.coords(self.low_handle)[0]
        hx = self.canvas.coords(self.high_handle)[0]
        # choose nearest handle
        if abs(x - lx) < abs(x - hx):
            self.active_handle = 'low'
        else:
            self.active_handle = 'high'
    
    def on_drag(self, event):
        # use actual canvas width for boundaries (handles may not span full len)
        max_w = self.canvas.winfo_width()
        x = min(max(event.x, 0), max_w)
        if self.active_handle == 'low':
            # ensure not past high handle
            hx = self.canvas.coords(self.high_handle)[0]
            x = min(x, hx - 10)
            self.canvas.coords(self.low_handle, x, 0, x + 10, 20)
            self.low = int(self.min_val + x / max_w * (self.max_val - self.min_val))
            # ensure min gap of 5 between low and high
            if self.high - self.low < 5:
                self.low = self.high - 5
                if self.low < self.min_val:
                    self.low = self.min_val
                # reposition handle to match adjusted value
                new_x = (self.low - self.min_val) / (self.max_val - self.min_val) * max_w
                self.canvas.coords(self.low_handle, new_x, 0, new_x + 10, 20)
        else:
            lx = self.canvas.coords(self.low_handle)[0] + 10
            x = max(x, lx)
            self.canvas.coords(self.high_handle, x - 10, 0, x, 20)
            self.high = int(self.min_val + x / max_w * (self.max_val - self.min_val))
            # ensure min gap of 5 between low and high
            if self.high - self.low < 5:
                self.high = self.low + 5
                if self.high > self.max_val:
                    self.high = self.max_val
                # reposition handle to match adjusted value
                new_x = (self.high - self.min_val) / (self.max_val - self.min_val) * max_w
                self.canvas.coords(self.high_handle, new_x - 10, 0, new_x, 20)
        # update selection bar
        low_x = self.canvas.coords(self.low_handle)[0] + 5
        high_x = self.canvas.coords(self.high_handle)[2] - 5
        self.canvas.coords(self.selection, low_x, self.track_y1, high_x, self.track_y2)
        self.command()
    
    def get(self):
        return self.low, self.high

    def _on_resize(self, event):
        # canvas width changed, extend track and reposition handles
        new_w = event.width
        self.length = new_w
        self.canvas.coords(self.track, 0, self.track_y1, new_w, self.track_y2)
        # reposition handles based on current values
        self.set(self.low, self.high)
    
    def set(self, low, high):
        self.low = low
        self.high = high
        # use current width to compute positions
        max_w = self.canvas.winfo_width()
        # adjust track to full width as well
        self.canvas.coords(self.track, 0, self.track_y1, max_w, self.track_y2)
        low_x = (low - self.min_val) / (self.max_val - self.min_val) * max_w
        high_x = (high - self.min_val) / (self.max_val - self.min_val) * max_w
        self.canvas.coords(self.low_handle, low_x, 0, low_x + 10, 20)
        self.canvas.coords(self.high_handle, high_x - 10, 0, high_x, 20)
        # update selection bar as well
        sel_low = low_x + 5
        sel_high = high_x - 5
        # use right edge of high handle for selection extents
        sel_high = self.canvas.coords(self.high_handle)[2] - 5
        self.canvas.coords(self.selection, sel_low, self.track_y1, sel_high, self.track_y2)


class ColorDetectorUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Circle Color Detector - Control Panel")
        self.root.geometry("1200x700")
        
        # Initialize GPIO
        self.init_gpio()
        
        # Video capture - Fast initialization with DirectShow (Windows)
        # Try camera index 0 first (most common), with DirectShow backend for speed
        print("Initializing camera...")
        self.cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)  # CAP_DSHOW speeds up on Windows
        if not self.cap.isOpened():
            # Fallback to index 1 if 0 fails
            print("Trying camera index 0...")
            self.cap = cv2.VideoCapture(0)
        
        if self.cap.isOpened():
            print(f"✓ Camera opened successfully")
        else:
            print("✗ Warning: Failed to open camera")
        
        # Current selected color (0 = red, 1 = blue)
        self.current_color = 0
        # threshold used to decide big vs small circle
        self.size_threshold = 40
        
        # Lock state and recorded circles
        self.is_locked = False
        self.recorded_left = None   # Will store (color, size) tuple when locked
        self.recorded_right = None  # Will store (color, size) tuple when locked
        
        # Last detected circles (persist across frames even if not currently detected)
        self.last_detected_left = None   # Last detected left circle
        self.last_detected_right = None  # Last detected right circle

        # Grip sequence state
        self.grip_in_progress = False

        # Pneumatic timing state
        self.max_inflate_time = 4.0
        self.accumulated_inflate_time = 0.0
        self.inflate_time_lock = threading.Lock()
        
        # HSV ranges for each color (no radius here)
        self.hsv_ranges = {
            0: {'name': 'RED', 'low': [0, 100, 100], 'high': [10, 255, 255]},
            1: {'name': 'BLUE', 'low': [100, 100, 100], 'high': [130, 255, 255]}
        }
        
        # Create main layout
        self.create_layout()
        self.running = True
        
        # Start video capture thread
        self.video_thread = threading.Thread(target=self.video_loop, daemon=True)
        self.video_thread.start()
        
        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def init_gpio(self):
        """Initialize GPIO pins for pneumatic valve control"""
        try:
            if HAS_GPIO:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
            else:
                print("[INFO] Running with mock GPIO (Windows development mode)")
                GPIO.setmode = lambda x: None
            
            # Set up grip pins as outputs
            for pin in GRIP_PINS.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            
            # Set up signal pin
            GPIO.setup(SIGNAL_PIN, GPIO.OUT)
            GPIO.output(SIGNAL_PIN, GPIO.HIGH)
            
            # Set up pump control pins
            GPIO.setup(PUMP_FWD, GPIO.OUT)
            GPIO.setup(PUMP_BWD, GPIO.OUT)
            GPIO.output(PUMP_FWD, GPIO.LOW)
            GPIO.output(PUMP_BWD, GPIO.LOW)
            
            # Set up pump control pins
            GPIO.setup(VALVE_FWD, GPIO.OUT)
            GPIO.setup(VALVE_BWD, GPIO.OUT)
            GPIO.output(VALVE_FWD, GPIO.LOW)
            GPIO.output(VALVE_BWD, GPIO.LOW)
            
            # Set up position control pins
            GPIO.setup(CIRCLE_BIT0, GPIO.OUT)
            GPIO.setup(CIRCLE_BIT1, GPIO.OUT)
            GPIO.setup(POSITION_TRIGGER, GPIO.OUT)
            GPIO.setup(STATE_TRIGGER, GPIO.OUT)
            GPIO.output(CIRCLE_BIT0, GPIO.HIGH)  # HIGH = inactive
            GPIO.output(CIRCLE_BIT1, GPIO.HIGH)  # HIGH = inactive
            GPIO.output(POSITION_TRIGGER, GPIO.HIGH)  # HIGH = inactive
            GPIO.output(STATE_TRIGGER, GPIO.HIGH)  # Default LOW; pulse HIGH as completion ACK

            # External trigger input (GPIO4)
            if HAS_GPIO:
                GPIO.setup(READY_INPUT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            else:
                GPIO.setup(READY_INPUT_PIN, GPIO.IN)
        except Exception as e:
            print(f"[WARNING] GPIO initialization error: {e}")

    def _pulse_state_high(self, duration=1):
        """Send GPIO21 HIGH pulse for acknowledgment."""
        GPIO.output(STATE_TRIGGER, GPIO.LOW)
        time.sleep(1)
        GPIO.output(STATE_TRIGGER, GPIO.HIGH)

    def _wait_for_ready_high(self, timeout=15.0, stage_text=''):
        """Wait for one rising event on GPIO4; returns True when received."""
        if not HAS_GPIO:
            # In Windows mock mode, auto-pass after a short delay for workflow testing.
            print(f"[MOCK] {stage_text} Simulated GPIO4 HIGH received")
            time.sleep(0.3)
            return True

        deadline = time.time() + timeout
        while self.running and time.time() < deadline:
            if GPIO.input(READY_INPUT_PIN) == GPIO.HIGH:
                # Wait until signal falls back to LOW to avoid counting one level twice.
                while self.running and GPIO.input(READY_INPUT_PIN) == GPIO.HIGH:
                    time.sleep(0.01)
                return True
            time.sleep(0.01)
        return False

    def _send_single_circle_code(self, side, circle_info):
        """Send circle type bits and side selection once."""
        if not circle_info:
            return

        code = CIRCLE_CODE_MAP.get(circle_info)
        if code is None:
            print(f"[CIRCLE SIGNAL] Unknown type: {circle_info}")
            return

        bit0, bit1 = code
        side_level = GPIO.HIGH if side == 'left' else GPIO.LOW
        GPIO.output(POSITION_TRIGGER, side_level)
        GPIO.output(CIRCLE_BIT0, GPIO.LOW if bit0 else GPIO.HIGH)
        GPIO.output(CIRCLE_BIT1, GPIO.LOW if bit1 else GPIO.HIGH)

        # Active-low strobe on SIGNAL_PIN to latch code on receiver side.
        GPIO.output(SIGNAL_PIN, GPIO.LOW)
        time.sleep(0.2)
        GPIO.output(SIGNAL_PIN, GPIO.HIGH)
        print(f"[CIRCLE SIGNAL] side={side}, type={circle_info}, code={bit0}{bit1}")

    def send_locked_circle_signals(self):
        """After locking, send currently known circle info before grip action."""
        left_info = self.recorded_left or self.last_detected_left
        right_info = self.recorded_right or self.last_detected_right

        if not left_info and not right_info:
            print("[CIRCLE SIGNAL] No circle info available to send")
            return

        self._send_single_circle_code('left', left_info)
        time.sleep(0.1)
        self._send_single_circle_code('right', right_info)
    
    def grip_object(self, color, size):
        """Activate grip for a specific color and size circle"""
        key = f'{color}_{size}'
        if key in GRIP_PINS:
            pin = GRIP_PINS[key]
            GPIO.output(pin, GPIO.LOW)  # LOW activates (inverse logic)
            time.sleep(0.5)
            GPIO.output(pin, GPIO.HIGH)
            print(f"Gripped {key}")
    
    def pump_inflate(self, duration):
        """Control pump to inflate for specified duration (seconds)"""
        GPIO.output(PUMP_FWD, GPIO.LOW)  # LOW activates
        GPIO.output(PUMP_BWD, GPIO.HIGH)  # HIGH deactivates
        time.sleep(duration)
        GPIO.output(PUMP_FWD, GPIO.HIGH)  # HIGH deactivates
        print(f"Inflated for {duration}s")
    
    def pump_deflate(self, duration):
        """Control pump to deflate for specified duration (seconds)"""
        GPIO.output(VALVE_FWD, GPIO.LOW)  # LOW activates
        GPIO.output(VALVE_BWD, GPIO.HIGH)  # HIGH deactivates
        time.sleep(duration)
        GPIO.output(VALVE_FWD, GPIO.HIGH)  # HIGH deactivates
        print(f"Deflated for {duration}s")
    
    def send_signal(self, state):
        """Send signal (HIGH or LOW) via signal pin"""
        GPIO.output(SIGNAL_PIN, GPIO.LOW if state else GPIO.HIGH)  # LOW for active
        print(f"Signal sent: {'LOW' if state else 'HIGH'}")
    
    def cleanup_gpio(self):
        """Clean up all GPIO pins"""
        try:
            GPIO.output(PUMP_FWD, GPIO.HIGH)  # HIGH deactivates
            GPIO.output(PUMP_BWD, GPIO.HIGH)  # HIGH deactivates

            
            GPIO.output(VALVE_FWD, GPIO.LOW)  # LOW activates to deflate
            GPIO.output(VALVE_BWD, GPIO.HIGH)  # HIGH deactivates
            time.sleep(1)
            GPIO.output(VALVE_FWD, GPIO.HIGH)  # HIGH deactivates
            
            if HAS_GPIO:
                GPIO.cleanup()
            print("[INFO] GPIO cleaned up")
        except Exception as e:
            print(f"[WARNING] GPIO cleanup error: {e}")
            GPIO.setwarnings = lambda x: None
    
    
    def create_layout(self):
        # Main container
        main_frame = Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left side (video + mask)
        left_frame = Frame(main_frame, bg='white')
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Left top - Camera feed
        camera_label = Label(left_frame, text="CAMERA FEED", bg='white', fg='black', height=2,
                             font=("Arial", 16, "bold"))
        camera_label.pack()
        self.camera_panel = Label(left_frame, bg='white', width=640, height=360)
        self.camera_panel.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 5))
        
        # Lock button and display area below camera
        lock_frame = Frame(left_frame, bg='#f0f0f0')
        lock_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.lock_btn = Button(lock_frame, text="🔓 LOCK CIRCLES", bg='#4CAF50', fg='white', 
                               font=("Arial", 13, "bold"), command=self.toggle_lock)
        self.lock_btn.pack(side=tk.LEFT, padx=10, pady=5)
        
        # Display recorded circles
        record_display = Frame(lock_frame, bg='#f0f0f0')
        record_display.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        
        Label(record_display, text="LEFT:", bg='#f0f0f0', font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        self.left_circle_label = Label(record_display, text="Not Locked", bg='#f0f0f0', 
                                       font=("Arial", 11), width=18)
        self.left_circle_label.pack(side=tk.LEFT, padx=5)
        
        Label(record_display, text="RIGHT:", bg='#f0f0f0', font=("Arial", 11, "bold")).pack(side=tk.LEFT)
        self.right_circle_label = Label(record_display, text="Not Locked", bg='#f0f0f0', 
                                        font=("Arial", 11), width=18)
        self.right_circle_label.pack(side=tk.LEFT, padx=5)
        
        # Left bottom - Mask
        mask_label = Label(left_frame, text="COLOR MASK", bg='white', fg='black', height=2,
                           font=("Arial", 16, "bold"))
        mask_label.pack()
        self.mask_panel = Label(left_frame, bg='white', width=640, height=360)
        self.mask_panel.pack(fill=tk.BOTH, expand=True, padx=0)
        
        # Right side (controls) background white
        right_frame = Frame(main_frame, bg='white')
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        
        # Right top - HSV controls header
        control_label = Label(right_frame, text="HSV ADJUSTMENT", bg='white', fg='black', height=2,
                               font=("Arial", 16, "bold"))
        control_label.pack(fill=tk.X, pady=(0, 10))
        
        self.hsv_sliders = {}
        hsv_frame = Frame(right_frame, bg='white')
        hsv_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Color name display (top of controls)
        self.color_name_label = Label(hsv_frame, text="", font=("Arial", 14, "bold"), bg='#d3d3d3')
        self.color_name_label.pack(pady=10)
        
        # Create slider rows using DualSlider
        def create_slider_row(parent, label, max_val, key_low, key_high):
            # container for one parameter, use grey background so bars appear contiguous
            row = Frame(parent, bg='#dddddd')
            row.pack(fill=tk.X, padx=10, pady=5)
            # top part: min/max labels match row color
            top = Frame(row, bg='#dddddd')
            top.pack(fill=tk.X)
            Label(top, text=f"{label} MIN:", bg='#dddddd', font=("Arial", 10, "bold")).pack(side=tk.LEFT)
            min_lbl = Label(top, text="0", bg='#dddddd', width=4, font=("Arial", 10))
            min_lbl.pack(side=tk.LEFT)
            Label(top, text=f" {label} MAX:", bg='#dddddd', font=("Arial", 10, "bold")).pack(side=tk.LEFT)
            max_lbl = Label(top, text="0", bg='#dddddd', width=4, font=("Arial", 10))
            max_lbl.pack(side=tk.LEFT)
            setattr(self, f'{key_low}_label', min_lbl)
            setattr(self, f'{key_high}_label', max_lbl)
            
            # slider itself
            ds = DualSlider(row, 0, max_val, length=200, command=self.update_hsv, bg='#dddddd')
            ds.pack(fill=tk.X, expand=True, pady=(2,0))
            self.hsv_sliders[key_low] = ds
            self.hsv_sliders[key_high] = ds
            return ds, (min_lbl, max_lbl)
        
        # Hue (0-179)
        create_slider_row(hsv_frame, "H", 179, 'h_low', 'h_high')
        # Saturation (0-255)
        create_slider_row(hsv_frame, "S", 255, 's_low', 's_high')
        # Value (0-255)
        create_slider_row(hsv_frame, "V", 255, 'v_low', 'v_high')

        
        # Right middle - size threshold control
        size_label = Label(right_frame, text="SIZE THRESHOLD (pixels)", bg='white', fg='black', height=2,
                            font=("Arial", 16, "bold"))
        size_label.pack(fill=tk.X, pady=(10, 0))
        # scale for threshold
        self.size_scale = Scale(right_frame, from_=5, to=100, orient=HORIZONTAL,
                        label="Larger = Big Circle", command=self.update_threshold,
                        bg='#d3d3d3', troughcolor='#cccccc', font=("Arial", 11))
        self.size_scale.set(40)
        self.size_scale.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Right bottom - Button controls
        button_label = Label(right_frame, text="CONTROL BUTTONS", bg='white', fg='black', height=2,
                             font=("Arial", 16, "bold"))
        button_label.pack(fill=tk.X, pady=(0, 10))
        
        button_frame = Frame(right_frame, bg='white')
        button_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.red_btn = Button(button_frame, text="Adjust RED", bg="#FF0000", fg='white', font=("Arial", 12, "bold"), command=self.select_red)
        self.red_btn.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        self.blue_btn = Button(button_frame, text="Adjust BLUE", bg="#2F00FF", fg='white', font=("Arial", 12, "bold"), command=self.select_blue)
        self.blue_btn.pack(fill=tk.X, padx=10, pady=(0, 5))
        
        # Position and grip control buttons (3 buttons in one row)
        position_control_frame = Frame(button_frame, bg='white')
        position_control_frame.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        Button(position_control_frame, text="LEFT", bg='#FF9800', fg='white', font=("Arial", 12, "bold"), 
               command=self.send_left_position).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        Button(position_control_frame, text="RIGHT", bg='#FF9800', fg='white', font=("Arial", 12, "bold"), 
               command=self.send_right_position).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))
        Button(position_control_frame, text="GRIP", bg='#FF9800', fg='white', font=("Arial", 12, "bold"), 
               command=self.send_grip).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        
        # Exit button
        Button(button_frame, text="EXIT", bg='#888888', fg='white', font=("Arial", 11, "bold"), command=self.on_closing).pack(fill=tk.X, padx=10, pady=(10, 10))
        
        # Pneumatic valve control section
        pneumatic_label = Label(right_frame, text="PNEUMATIC CONTROL", bg='white', fg='black', height=2,
                                font=("Arial", 16, "bold"))
        pneumatic_label.pack(fill=tk.X, pady=(10, 0))
        
        pneumatic_frame = Frame(right_frame, bg='white')
        pneumatic_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.inflate_time_label = Label(
            pneumatic_frame,
            text=f"Inflate Time: {self.accumulated_inflate_time:.1f}s / {self.max_inflate_time:.1f}s",
            bg='white',
            fg='black',
            font=("Arial", 12, "bold")
        )
        self.inflate_time_label.pack(fill=tk.X, padx=10, pady=(0, 5))
        
        # Inflate section
        inflate_frame = Frame(pneumatic_frame, bg='#dddddd')
        inflate_frame.pack(fill=tk.X, padx=10, pady=5)
        Label(inflate_frame, text="INFLATE (seconds)", bg='#dddddd', font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=5, pady=5)
        self.inflate_scale = Scale(inflate_frame, from_=0.1, to=4, resolution=0.1, orient=HORIZONTAL,
                                   bg='#d3d3d3', troughcolor='#cccccc', font=("Arial", 10))
        self.inflate_scale.set(1.0)
        self.inflate_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        Button(inflate_frame, text="RUN", bg='#90EE90', fg='black', font=("Arial", 11, "bold"),
               command=self.do_inflate).pack(side=tk.LEFT, padx=5)
        
        # Deflate section
        deflate_frame = Frame(pneumatic_frame, bg='#dddddd')
        deflate_frame.pack(fill=tk.X, padx=10, pady=5)
        Label(deflate_frame, text="DEFLATE (fixed 1s)", bg='#dddddd', font=("Arial", 12, "bold")).pack(side=tk.LEFT, padx=5, pady=5)
        Button(deflate_frame, text="RUN", bg='#FFB6C1', fg='black', font=("Arial", 11, "bold"),
               command=self.do_deflate).pack(side=tk.LEFT, padx=5)
        
        # Initialize slider values
        self.update_slider_values()
    
    def update_slider_values(self):
        """Update slider values based on current color selection"""
        color_data = self.hsv_ranges[self.current_color]
        self.color_name_label.config(text=f"Current Color: {color_data['name']}")
        
        # set dual sliders
        self.hsv_sliders['h_low'].set(color_data['low'][0], color_data['high'][0])
        self.hsv_sliders['s_low'].set(color_data['low'][1], color_data['high'][1])
        self.hsv_sliders['v_low'].set(color_data['low'][2], color_data['high'][2])
        
        # update labels manually (min and max separately)
        self.h_low_label.config(text=f"{color_data['low'][0]}")
        self.h_high_label.config(text=f"{color_data['high'][0]}")
        self.s_low_label.config(text=f"{color_data['low'][1]}")
        self.s_high_label.config(text=f"{color_data['high'][1]}")
        self.v_low_label.config(text=f"{color_data['low'][2]}")
        self.v_high_label.config(text=f"{color_data['high'][2]}")
        
        if self.current_color == 0:
            self.red_btn.config(relief=tk.SUNKEN, bd=3)
            self.blue_btn.config(relief=tk.RAISED, bd=1)
        else:
            self.blue_btn.config(relief=tk.SUNKEN, bd=3)
            self.red_btn.config(relief=tk.RAISED, bd=1)
    
    def update_hsv(self, value=None):
        """Update HSV values from dual sliders"""
        color_data = self.hsv_ranges[self.current_color]
        h_low, h_high = self.hsv_sliders['h_low'].get()
        s_low, s_high = self.hsv_sliders['s_low'].get()
        v_low, v_high = self.hsv_sliders['v_low'].get()
        color_data['low'][0] = h_low
        color_data['high'][0] = h_high
        color_data['low'][1] = s_low
        color_data['high'][1] = s_high
        color_data['low'][2] = v_low
        color_data['high'][2] = v_high
        
        # Update labels separately
        self.h_low_label.config(text=f"{h_low}")
        self.h_high_label.config(text=f"{h_high}")
        self.s_low_label.config(text=f"{s_low}")
        self.s_high_label.config(text=f"{s_high}")
        self.v_low_label.config(text=f"{v_low}")
        self.v_high_label.config(text=f"{v_high}")
    
    def select_red(self):
        self.current_color = 0
        self.update_slider_values()
    
    def select_blue(self):
        self.current_color = 1
        self.update_slider_values()
    
    def reset_values(self):
        """Reset current color to default"""
        if self.current_color == 0:
            self.hsv_ranges[0] = {'name': '紅色', 'low': [0, 100, 100], 'high': [10, 255, 255]}
        else:
            self.hsv_ranges[1] = {'name': '藍色', 'low': [100, 100, 100], 'high': [130, 255, 255]}
        self.update_slider_values()
    
    def do_inflate(self):
        """Execute inflate for user-specified duration"""
        requested_duration = float(self.inflate_scale.get())

        with self.inflate_time_lock:
            remaining = self.max_inflate_time - self.accumulated_inflate_time
            if remaining <= 0:
                print(f"[INFLATE] Max total reached ({self.max_inflate_time:.1f}s). Please deflate to reset.")
                return

            duration = min(requested_duration, remaining)
            self.accumulated_inflate_time += duration
            self.inflate_time_label.config(
                text=f"Inflate Time: {self.accumulated_inflate_time:.1f}s / {self.max_inflate_time:.1f}s"
            )

        if duration < requested_duration:
            print(f"[INFLATE] Requested {requested_duration:.1f}s, capped to {duration:.1f}s to stay within total limit.")

        # Run in thread to avoid blocking UI
        threading.Thread(target=self.pump_inflate, args=(duration,), daemon=True).start()
    
    def do_deflate(self):
        """Execute fixed 1-second deflate and reset accumulated inflate timer"""
        with self.inflate_time_lock:
            self.accumulated_inflate_time = 0.0
            self.inflate_time_label.config(
                text=f"Inflate Time: {self.accumulated_inflate_time:.1f}s / {self.max_inflate_time:.1f}s"
            )

        duration = 1.0
        # Run in thread to avoid blocking UI
        threading.Thread(target=self.pump_deflate, args=(duration,), daemon=True).start()
    
    def send_left_position(self):
        """Send left position signal with circle code"""
        if self.grip_in_progress:
            print("[LEFT] Ignored: Grip in progress")
            return
        
        # Send circle signal for left position
        left_info = self.recorded_left or self.last_detected_left
        if left_info:
            self._send_single_circle_code('left', left_info)
        
        GPIO.output(POSITION_TRIGGER, GPIO.HIGH)
        print("[LEFT] Sent: GPIO20=LOW")
        GPIO.output(STATE_TRIGGER, GPIO.LOW)
        time.sleep(1)
        GPIO.output(STATE_TRIGGER, GPIO.HIGH)
    
    def send_right_position(self):
        """Send right position signal with circle code"""
        if self.grip_in_progress:
            print("[RIGHT] Ignored: Grip in progress")
            return
        
        # Send circle signal for right position
        right_info = self.recorded_right or self.last_detected_right
        if right_info:
            self._send_single_circle_code('right', right_info)
        
        GPIO.output(POSITION_TRIGGER, GPIO.LOW)
        print("[RIGHT] Sent: GPIO20=HIGH")
        GPIO.output(STATE_TRIGGER, GPIO.LOW)
        time.sleep(1)
        GPIO.output(STATE_TRIGGER, GPIO.HIGH)

    def _grip_sequence(self):
        """Two-stage ready handshake on GPIO4: inflate then deflate."""
        self.grip_in_progress = True
        try:
            print("[GRIP] Waiting for 1st GPIO4=HIGH (Inflate)")
            if not self._wait_for_ready_high(timeout=20.0, stage_text='1st'):
                print("[GRIP] 1st wait timeout, stopping process")
                return

            self.pump_inflate(3.0)
            self._pulse_state_high(1)
            print("[GRIP] Stage 1 complete: Inflated and sent GPIO21 HIGH for 1 sec")

            print("[GRIP] Waiting for 2nd GPIO4=HIGH (Deflate)")
            if not self._wait_for_ready_high(timeout=20.0, stage_text='2nd'):
                print("[GRIP] 2nd wait timeout, stopping process")
                return

            self.pump_deflate(1.0)
            self._pulse_state_high(1)
            print("[GRIP] Stage 2 complete: Deflated and sent GPIO21 HIGH for 1 sec")
        finally:
            self.grip_in_progress = False
    
    def send_grip(self):
        """Start grip flow controlled by two GPIO4 HIGH events."""
        if self.grip_in_progress:
            print("[GRIP] Ignored: Process already in progress")
            return
        
        self._pulse_state_high(1)
        threading.Thread(target=self._grip_sequence, daemon=True).start()
    
    def toggle_lock(self):
        """Toggle lock state and update recorded circles"""
        self.is_locked = not self.is_locked
        if self.is_locked:
            self.lock_btn.config(text="🔒         LOCK        ", bg='#f44336')
            # Snapshot current detections, don't send signal yet
            if self.last_detected_left:
                self.recorded_left = self.last_detected_left
            if self.last_detected_right:
                self.recorded_right = self.last_detected_right
        else:
            self.lock_btn.config(text="🔓 LOCK CIRCLES", bg='#4CAF50')
            # Clear recorded circles and last detected when unlocking
            self.recorded_left = None
            self.recorded_right = None
            self.last_detected_left = None
            self.last_detected_right = None
            self.left_circle_label.config(text="Not Locked")
            self.right_circle_label.config(text="Not Locked")
    

    def update_threshold(self, val):
        """Callback from size scale"""
        try:
            self.size_threshold = int(val)
        except ValueError:
            pass
    
    def convert_frame_to_photo(self, frame, width=640, height=360):
        """Convert OpenCV frame to PhotoImage"""
        frame = cv2.resize(frame, (width, height))
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb_frame)
        return ImageTk.PhotoImage(img)
    
    def video_loop(self):
        """Main video processing loop"""
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                print("Failed to grab frame")
                break
            
            # Convert to HSV
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            
            # Create masks for both colors
            mask_red = cv2.inRange(hsv, tuple(self.hsv_ranges[0]['low']), tuple(self.hsv_ranges[0]['high']))
            mask_blue = cv2.inRange(hsv, tuple(self.hsv_ranges[1]['low']), tuple(self.hsv_ranges[1]['high']))
            
            # Combine masks
            combined_mask = cv2.bitwise_or(mask_red, mask_blue)
            
            # Apply morphological operations to clean up mask
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel, iterations=2)
            # additional erosion/dilation
            combined_mask = cv2.erode(combined_mask, kernel, iterations=1)
            combined_mask = cv2.dilate(combined_mask, kernel, iterations=1)
            
            # Draw circles on the frame
            masked = cv2.bitwise_and(frame, frame, mask=combined_mask)
            gray = cv2.cvtColor(masked, cv2.COLOR_BGR2GRAY)
            # apply blur sequence to smooth edges
            gray = cv2.medianBlur(gray, 5)
            # gray = cv2.GaussianBlur(gray, (7, 7), 0)
            # gray = cv2.GaussianBlur(gray, (5, 5), 0)
            
            # remove radius restrictions to allow any size circle
            circles = cv2.HoughCircles(
                gray,
                cv2.HOUGH_GRADIENT,
                dp=1.2,
                minDist=30,
                param1=60, #看到很多鬼圓=>調高
                param2=30,
                minRadius=10,
            )
            
            # Get frame dimensions for center line
            frame_height, frame_width = frame.shape[:2]
            center_x = frame_width // 2
            
            # Draw center line
            cv2.line(frame, (center_x, 0), (center_x, frame_height), (255, 0, 255), 2)
            
            # Track circles on left and right for this frame
            current_left = None
            current_right = None
            
            # Filter overlapping circles (avoid convertScaleAbs which clamps coords)
            if circles is not None:
                # circles come as float32, shape (1, N, 3)
                circle_list = circles[0]  # shape (N,3)
                # sort by radius descending
                circle_list = circle_list[circle_list[:,2].argsort()[::-1]]
                
                filtered_circles = []
                for c in circle_list:
                    x_f, y_f, r_f = c
                    x, y, radius = int(round(x_f)), int(round(y_f)), int(round(r_f))
                    is_overlapping = False
                    for ax, ay, ar in filtered_circles:
                        dist = ((x-ax)**2 + (y-ay)**2) ** 0.5
                        if dist < (radius + ar) * 0.7:
                            is_overlapping = True
                            break
                    if not is_overlapping:
                        filtered_circles.append((x, y, radius))
                
                for x, y, radius in filtered_circles:
                    # Improved color detection: check multiple points around the circle
                    # to handle cases where center is not detected due to lighting
                    color_label = "unknown"
                    color_type = None
                    red_count = 0
                    blue_count = 0
                    
                    # Sample points: center + 8 points around the circle edge
                    sample_points = []
                    # Add center point with boundary check
                    if 0 <= y < frame_height and 0 <= x < frame_width:
                        sample_points.append((x, y))
                    # Add edge points
                    for angle in range(0, 360, 45):  # 8 directions
                        px = int(x + radius * 0.7 * np.cos(np.radians(angle)))
                        py = int(y + radius * 0.7 * np.sin(np.radians(angle)))
                        if 0 <= py < frame_height and 0 <= px < frame_width:
                            sample_points.append((px, py))
                    
                    # Count red and blue pixels in sample points
                    for px, py in sample_points:
                        # Extra safety check before accessing arrays
                        if 0 <= px < frame_width and 0 <= py < frame_height:
                            if mask_red[py, px] > 0:
                                red_count += 1
                            if mask_blue[py, px] > 0:
                                blue_count += 1
                    
                    # Determine color by majority vote
                    if red_count > blue_count and red_count > 0:
                        color_label = "RED"
                        color_type = "red"
                    elif blue_count > red_count and blue_count > 0:
                        color_label = "BLU"
                        color_type = "blue"
                    
                    # size classification using user-controlled threshold
                    size_label = "Big" if radius > self.size_threshold else "Small"
                    size_type = "big" if radius > self.size_threshold else "small"
                    text_label = f"{size_label} {color_label}"

                    cv2.circle(frame, (x, y), radius, (0, 255, 0), 2)
                    # put text above the circle
                    cv2.putText(frame, text_label, (x - radius, y - radius - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
                    
                    # Record circle position (left or right of center)
                    if color_type:
                        if x < center_x and current_left is None:
                            current_left = (color_type, size_type)
                        elif x >= center_x and current_right is None:
                            current_right = (color_type, size_type)
            
            # Update detection logic:
            # - Update last_detected when new circles are found
            # - If locked: save to recorded_left/right and display those
            # - If not locked: display last_detected to maintain stability
            
            # Update last detected if current frame has detections
            if current_left:
                self.last_detected_left = current_left
            if current_right:
                self.last_detected_right = current_right
            
            if self.is_locked:
                # When locked, save new detections only if not already recorded
                if current_left and self.recorded_left is None:
                    self.recorded_left = current_left
                if current_right and self.recorded_right is None:
                    self.recorded_right = current_right
                
                # Display locked values
                if self.recorded_left:
                    color, size = self.recorded_left
                    color_en = "RED" if color == "red" else "BLUE"
                    size_en = "BIG" if size == "big" else "SMALL"
                    self.left_circle_label.config(text=f"{color_en} {size_en}")
                
                if self.recorded_right:
                    color, size = self.recorded_right
                    color_en = "RED" if color == "red" else "BLUE"
                    size_en = "BIG" if size == "big" else "SMALL"
                    self.right_circle_label.config(text=f"{color_en} {size_en}")
            else:
                # When not locked, display last detected values (persist across frames)
                if self.last_detected_left:
                    color, size = self.last_detected_left
                    color_en = "RED" if color == "red" else "BLUE"
                    size_en = "BIG" if size == "big" else "SMALL"
                    self.left_circle_label.config(text=f"{color_en} {size_en}")
                else:
                    self.left_circle_label.config(text="Not Detected")
                
                if self.last_detected_right:
                    color, size = self.last_detected_right
                    color_en = "RED" if color == "red" else "BLUE"
                    size_en = "BIG" if size == "big" else "SMALL"
                    self.right_circle_label.config(text=f"{color_en} {size_en}")
                else:
                    self.right_circle_label.config(text="Not Detected")

            # Update UI
            try:
                camera_photo = self.convert_frame_to_photo(frame)
                self.camera_panel.config(image=camera_photo)
                self.camera_panel.image = camera_photo
                
                # Show current color mask
                if self.current_color == 0:
                    mask_display = mask_red
                else:
                    mask_display = mask_blue
                
                # Convert mask to 3-channel for display
                mask_3ch = cv2.cvtColor(mask_display, cv2.COLOR_GRAY2BGR)
                mask_photo = self.convert_frame_to_photo(mask_3ch)
                self.mask_panel.config(image=mask_photo)
                self.mask_panel.image = mask_photo
            except:
                pass
    
    def on_closing(self):
        """Clean up and close application"""
        self.running = False
        self.cleanup_gpio()
        if self.cap:
            self.cap.release()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ColorDetectorUI(root)
    root.mainloop()
