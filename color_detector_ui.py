import cv2
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

# GPIO Pin Configuration (Raspberry Pi 5 BCM)
GRIP_PINS = {
    'red_big': 17,      # RED BIG circle grip
    'red_small': 18,    # RED SMALL circle grip
    'blue_big': 22,     # BLUE BIG circle grip
    'blue_small': 23    # BLUE SMALL circle grip
}
SIGNAL_PIN = 24         # Send/Receive signal (HIGH/LOW)
PUMP_FWD = 25          # L298N IN1: Pump motor forward (inflate)
PUMP_BWD = 26          # L298N IN2: Pump motor backward (deflate)


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
        self.root.title("Color-based Circle Detector")
        self.root.geometry("1200x700")
        
        # Initialize GPIO
        self.init_gpio()
        
        # Video capture
        self.cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        print("Camera Opened?", self.cap.isOpened())
        
        # Current selected color (0 = red, 1 = blue)
        self.current_color = 0
        # threshold used to decide big vs small circle
        self.size_threshold = 40
        
        # HSV ranges for each color (no radius here)
        self.hsv_ranges = {
            0: {'name': '紅色', 'low': [0, 100, 100], 'high': [10, 255, 255]},
            1: {'name': '藍色', 'low': [100, 100, 100], 'high': [130, 255, 255]}
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
            
            # Set up grip pins as outputs
            for pin in GRIP_PINS.values():
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            
            # Set up signal pin
            GPIO.setup(SIGNAL_PIN, GPIO.OUT)
            GPIO.output(SIGNAL_PIN, GPIO.LOW)
            
            # Set up pump control pins
            GPIO.setup(PUMP_FWD, GPIO.OUT)
            GPIO.setup(PUMP_BWD, GPIO.OUT)
            GPIO.output(PUMP_FWD, GPIO.LOW)
            GPIO.output(PUMP_BWD, GPIO.LOW)
        except Exception as e:
            print(f"[WARNING] GPIO initialization error: {e}")
    
    def grip_object(self, color, size):
        """Activate grip for a specific color and size circle"""
        key = f'{color}_{size}'
        if key in GRIP_PINS:
            pin = GRIP_PINS[key]
            GPIO.output(pin, GPIO.HIGH)
            time.sleep(0.5)
            GPIO.output(pin, GPIO.LOW)
            print(f"Gripped {key}")
    
    def pump_inflate(self, duration):
        """Control pump to inflate for specified duration (seconds)"""
        GPIO.output(PUMP_FWD, GPIO.HIGH)
        GPIO.output(PUMP_BWD, GPIO.LOW)
        time.sleep(duration)
        GPIO.output(PUMP_FWD, GPIO.LOW)
        print(f"Inflated for {duration}s")
    
    def pump_deflate(self, duration):
        """Control pump to deflate for specified duration (seconds)"""
        GPIO.output(PUMP_FWD, GPIO.LOW)
        GPIO.output(PUMP_BWD, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(PUMP_BWD, GPIO.LOW)
        print(f"Deflated for {duration}s")
    
    def send_signal(self, state):
        """Send signal (HIGH or LOW) via signal pin"""
        GPIO.output(SIGNAL_PIN, GPIO.HIGH if state else GPIO.LOW)
        print(f"Signal sent: {'HIGH' if state else 'LOW'}")
    
    def cleanup_gpio(self):
        """Clean up all GPIO pins"""
        try:
            GPIO.output(PUMP_FWD, GPIO.LOW)
            GPIO.output(PUMP_BWD, GPIO.LOW)
            if HAS_GPIO:
                GPIO.cleanup()
            print("[INFO] GPIO cleaned up")
        except Exception as e:
            print(f"[WARNING] GPIO cleanup error: {e}")
    
    
    def create_layout(self):
        # Main container
        main_frame = Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Left side (video + mask)
        left_frame = Frame(main_frame, bg='white')
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Left top - Camera feed
        camera_label = Label(left_frame, text="攝影機", bg='white', fg='black', height=2,
                             font=("Arial", 14, "bold"))
        camera_label.pack()
        self.camera_panel = Label(left_frame, bg='white', width=640, height=360)
        self.camera_panel.pack(fill=tk.BOTH, expand=True, padx=0, pady=(0, 5))
        
        # Left bottom - Mask
        mask_label = Label(left_frame, text="遮罩", bg='white', fg='black', height=2,
                           font=("Arial", 14, "bold"))
        mask_label.pack()
        self.mask_panel = Label(left_frame, bg='white', width=640, height=360)
        self.mask_panel.pack(fill=tk.BOTH, expand=True, padx=0)
        
        # Right side (controls) background white
        right_frame = Frame(main_frame, bg='white')
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        
        # Right top - HSV controls header
        control_label = Label(right_frame, text="HSV調整", bg='white', fg='black', height=2,
                               font=("Arial", 14, "bold"))
        control_label.pack(fill=tk.X, pady=(0, 10))
        
        self.hsv_sliders = {}
        hsv_frame = Frame(right_frame, bg='white')
        hsv_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Color name display (top of controls)
        self.color_name_label = Label(hsv_frame, text="", font=("Arial", 12, "bold"), bg='#d3d3d3')
        self.color_name_label.pack(pady=10)
        
        # Create slider rows using DualSlider
        def create_slider_row(parent, label, max_val, key_low, key_high):
            # container for one parameter, use grey background so bars appear contiguous
            row = Frame(parent, bg='#dddddd')
            row.pack(fill=tk.X, padx=10, pady=5)
            # top part: min/max labels match row color
            top = Frame(row, bg='#dddddd')
            top.pack(fill=tk.X)
            Label(top, text=f"{label}_min:", bg='#dddddd', font=("Arial", 9)).pack(side=tk.LEFT)
            min_lbl = Label(top, text="0", bg='#dddddd', width=4, font=("Arial", 9))
            min_lbl.pack(side=tk.LEFT)
            Label(top, text=f" {label}_max:", bg='#dddddd', font=("Arial", 9)).pack(side=tk.LEFT)
            max_lbl = Label(top, text="0", bg='#dddddd', width=4, font=("Arial", 9))
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
        size_label = Label(right_frame, text="大小閾值 (像素)", bg='white', fg='black', height=2,
                            font=("Arial", 14, "bold"))
        size_label.pack(fill=tk.X, pady=(10, 0))
        # scale for threshold
        self.size_scale = Scale(right_frame, from_=5, to=100, orient=HORIZONTAL,
                        label="大於多少視為大圈", command=self.update_threshold,
                        bg='#d3d3d3', troughcolor='#cccccc')
        self.size_scale.set(40)
        self.size_scale.pack(fill=tk.X, padx=10, pady=(0, 10))

        # Right bottom - Button controls
        button_label = Label(right_frame, text="功能按鍵", bg='white', fg='black', height=2,
                             font=("Arial", 14, "bold"))
        button_label.pack(fill=tk.X, pady=(0, 10))
        
        button_frame = Frame(right_frame, bg='white')
        button_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.red_btn = Button(button_frame, text="調整紅色", bg='#888888', fg='white', font=("Arial", 12, "bold"), command=self.select_red)
        self.red_btn.pack(fill=tk.X, padx=10, pady=(10, 5))
        
        self.blue_btn = Button(button_frame, text="調整藍色", bg='#888888', fg='white', font=("Arial", 12, "bold"), command=self.select_blue)
        self.blue_btn.pack(fill=tk.X, padx=10, pady=(0, 5))
        
        # Save / Reset buttons
        Button(button_frame, text="重置設定", bg='#888888', fg='white', font=("Arial", 10), command=self.reset_values).pack(fill=tk.X, padx=10, pady=(10, 5))
        Button(button_frame, text="離開", bg='#888888', fg='white', font=("Arial", 10, "bold"), command=self.on_closing).pack(fill=tk.X, padx=10, pady=(10, 10))
        
        # Pneumatic valve control section
        pneumatic_label = Label(right_frame, text="氣動控制", bg='white', fg='black', height=2,
                                font=("Arial", 14, "bold"))
        pneumatic_label.pack(fill=tk.X, pady=(10, 0))
        
        pneumatic_frame = Frame(right_frame, bg='white')
        pneumatic_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Inflate section
        inflate_frame = Frame(pneumatic_frame, bg='#dddddd')
        inflate_frame.pack(fill=tk.X, padx=10, pady=5)
        Label(inflate_frame, text="打氣 (秒數)", bg='#dddddd', font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=5, pady=5)
        self.inflate_scale = Scale(inflate_frame, from_=0.1, to=10, resolution=0.1, orient=HORIZONTAL,
                                   bg='#d3d3d3', troughcolor='#cccccc')
        self.inflate_scale.set(1.0)
        self.inflate_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        Button(inflate_frame, text="執行", bg='#90EE90', fg='black', font=("Arial", 10, "bold"),
               command=self.do_inflate).pack(side=tk.LEFT, padx=5)
        
        # Deflate section
        deflate_frame = Frame(pneumatic_frame, bg='#dddddd')
        deflate_frame.pack(fill=tk.X, padx=10, pady=5)
        Label(deflate_frame, text="洩氣 (秒數)", bg='#dddddd', font=("Arial", 11, "bold")).pack(side=tk.LEFT, padx=5, pady=5)
        self.deflate_scale = Scale(deflate_frame, from_=0.1, to=10, resolution=0.1, orient=HORIZONTAL,
                                   bg='#d3d3d3', troughcolor='#cccccc')
        self.deflate_scale.set(1.0)
        self.deflate_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        Button(deflate_frame, text="執行", bg='#FFB6C1', fg='black', font=("Arial", 10, "bold"),
               command=self.do_deflate).pack(side=tk.LEFT, padx=5)
        
        # Initialize slider values
        self.update_slider_values()
    
    def update_slider_values(self):
        """Update slider values based on current color selection"""
        color_data = self.hsv_ranges[self.current_color]
        self.color_name_label.config(text=f"調整目標：{color_data['name']}")
        
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
        duration = self.inflate_scale.get()
        # Run in thread to avoid blocking UI
        threading.Thread(target=self.pump_inflate, args=(duration,), daemon=True).start()
    
    def do_deflate(self):
        """Execute deflate for user-specified duration"""
        duration = self.deflate_scale.get()
        # Run in thread to avoid blocking UI
        threading.Thread(target=self.pump_deflate, args=(duration,), daemon=True).start()
    

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
                    # determine which mask the center pixel belongs to
                    color_label = "unknown"
                    if mask_red[y, x] > 0:
                        color_label = "RC"
                    elif mask_blue[y, x] > 0:
                        color_label = "BC"
                    # size classification using user-controlled threshold
                    size_label = "Big" if radius > self.size_threshold else "Small"
                    text_label = f"{size_label} {color_label}"

                    cv2.circle(frame, (x, y), radius, (0, 255, 0), 2)
                    # put text above the circle
                    cv2.putText(frame, text_label, (x - radius, y - radius - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

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
