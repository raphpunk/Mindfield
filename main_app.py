import tkinter as tk
from tkinter import messagebox, filedialog, ttk, simpledialog
import tkinter.scrolledtext as scrolledtext
import threading
from collections import deque
import os
import asyncio
import logging
from datetime import datetime
import time
import subprocess
try:
    import dbus
except Exception:
    dbus = None
import csv
import json
from hrv_manager import HRVDeviceManager
from rng_collector import RNGCollector
from aqrng import get_random_bytes
from group_session import GroupSessionManager
from queue import Queue, Empty
import getpass
import pathlib
import tkinter.font as tkfont

# Optional matplotlib for embedded realtime HRV plotting (best-effort)
try:
    import matplotlib
    # prefer the TkAgg backend when available for embedding in Tkinter
    try:
        matplotlib.use('TkAgg')
    except Exception:
        pass
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    _MPL_AVAILABLE = True
except Exception:
    Figure = None
    FigureCanvasTkAgg = None
    _MPL_AVAILABLE = False

# Module-level logger
logger = logging.getLogger('mindfield')
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    try:
        root = pathlib.Path(__file__).resolve().parent
        logpath = root / 'mindfield.log'
        fh = logging.FileHandler(logpath, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        # fallback to basic config
        logging.basicConfig(level=logging.DEBUG)

class ConsciousnessLab:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("mindfield-core")
        # Larger default size and enforce a reasonable minimum so controls are visible
        self.root.geometry("900x700")
        try:
            self.root.minsize(800, 600)
        except Exception:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Core components
        self.coherence_queue = Queue()
#        self.hrv_manager = HRVDeviceManager()
#        self.hrv_manager = HRVDeviceManager()
        self.hrv_manager = HRVDeviceManager(self.coherence_queue)  
        self.rng_collector = RNGCollector()
        # Start HRV -> RNG correlation consumer thread
        try:
            self._hrv_thread = threading.Thread(target=self._hrv_consumer, daemon=True)
            self._hrv_thread.start()
        except Exception:
            self._hrv_thread = None
        # Realtime HRV coherence history for sparkline
        self._hrv_coherence_history = deque(maxlen=200)
        self._hrv_sparkline_enabled = True
        self.group_manager = None
        # SDR instance placeholder (created lazily by provider)
        self._sdr_instance = None
        # Measured peak smoothing and spectral-enable flag
        self._sdr_measured_ema = None
        self._sdr_ema_alpha = 0.25
        self._sdr_spectral_enabled_var = tk.BooleanVar(value=True)
        self._sdr_spectral_enabled = True
        # SDR failure/backoff tracking for resilience
        self._sdr_fail_count = 0
        self._sdr_fail_threshold = 3
        self._sdr_fail_backoff_secs = 300  # 5 minutes
        self._sdr_disabled_until = 0
        
        # State
        self.device_vars = []
        self.running = False
        self.session_data = []
        self.current_session_type = "individual"
        
        self.setup_gui()
        # Honor environment override for admin mode on startup for testing
        try:
            env_mode = os.environ.get('MINDFIELD_ADMIN_MODE')
            if env_mode:
                try:
                    self.set_admin_mode(env_mode)
                except Exception:
                    pass
        except Exception:
            pass

        self.update_loop()
        

    def setup_gui(self):
        # Visual theme
        self.bg_color = "#f7f9fb"
        self.panel_color = "#ffffff"
        self.accent_color = "#3498db"
        # Create scalable named fonts so we can adjust sizes on window resize
        try:
            # Slightly smaller, tighter fonts for compact UI
            self.title_font = tkfont.Font(family="Helvetica", size=16, weight="bold")
            self.header_font = tkfont.Font(family="Helvetica", size=11, weight="bold")
            self.stats_font = tkfont.Font(family="Helvetica", size=12)
            self.small_font = tkfont.Font(family="Arial", size=9)
        except Exception:
            # fallback to tuples if tkfont not available
            self.title_font = ("Helvetica", 18, "bold")
            self.header_font = ("Helvetica", 12, "bold")
            self.stats_font = ("Helvetica", 14)
            self.small_font = ("Arial", 11)
        self.root.configure(bg=self.bg_color)

        # ttk style
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        # Compact button styling
        try:
            style.configure('TButton', font=self.small_font, padding=3)
        except Exception:
            pass
        style.configure('Accent.TButton', background=self.accent_color, foreground='white', padding=4)

        # Generate small circular icons for buttons (keeps references on self)
        self._icons = {}
        def _make_icon(color, size=16):
            img = tk.PhotoImage(width=size, height=size)
            r = size // 2
            cx = cy = r
            bg = self.bg_color
            for x in range(size):
                for y in range(size):
                    # simple circle mask
                    if (x - cx) ** 2 + (y - cy) ** 2 <= (r - 1) ** 2:
                        try:
                            img.put(color, (x, y))
                        except Exception:
                            pass
                    else:
                        try:
                            img.put(bg, (x, y))
                        except Exception:
                            pass
            return img

        # make slightly smaller icons to match compact buttons
        self._icons['scan'] = _make_icon('#3498db', size=14)
        self._icons['baseline'] = _make_icon('#9b59b6')
        self._icons['experiment'] = _make_icon('#27ae60')
        self._icons['seed'] = _make_icon('#8e44ad')
        self._icons['bt'] = _make_icon('#16a085')
        self._icons['export'] = _make_icon('#34495e')
        self._icons['group'] = _make_icon('#f39c12')

        # Body layout: left sidebar for controls, right content area for data panels
        self.body_frame = tk.Frame(self.root, bg=self.bg_color)
        self.body_frame.pack(side='top', fill='both', expand=True)

        self.sidebar = tk.Frame(self.body_frame, bg=self.bg_color, padx=8, pady=8)
        self.sidebar.pack(side='left', fill='y')

        self.content_frame = tk.Frame(self.body_frame, bg=self.bg_color)
        self.content_frame.pack(side='right', fill='both', expand=True)

        # Create a scrollable main area inside the content frame for the data panels
        self.main_container = tk.Frame(self.content_frame, bg=self.bg_color)
        self.main_container.pack(fill='both', expand=True, padx=0, pady=0)


        self._canvas = tk.Canvas(self.main_container, bg=self.bg_color, highlightthickness=0)
        vsb = tk.Scrollbar(self.main_container, orient='vertical', command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        self._canvas.pack(side='left', fill='both', expand=True)

        self.main_inner = tk.Frame(self._canvas, bg=self.bg_color)
        # keep the window id so we can update its width when the canvas resizes
        self._inner_window = self._canvas.create_window((0, 0), window=self.main_inner, anchor='nw')

        # Ensure the inner frame width matches the canvas width so widgets can use pack/grid naturally
        def _on_canvas_config(event):
            try:
                self._canvas.itemconfig(self._inner_window, width=event.width)
            except Exception:
                pass

        self._canvas.bind('<Configure>', _on_canvas_config)

        def _on_frame_configure(event):
            try:
                bbox = self._canvas.bbox("all")
                if bbox is None:
                    # Nothing laid out yet; fall back to inner frame size
                    w = self._canvas.winfo_width() or (event.width if hasattr(event, 'width') else 1)
                    h = self.main_inner.winfo_height() or (event.height if hasattr(event, 'height') else 1)
                    bbox = (0, 0, w, h)
                else:
                    # Ensure top is not negative which can cause unbounded scrolling
                    x0, y0, x1, y1 = bbox
                    if y0 < 0:
                        y0 = 0
                        bbox = (x0, y0, x1, y1)
                self._canvas.configure(scrollregion=bbox)
            except Exception:
                pass

        self.main_inner.bind("<Configure>", _on_frame_configure)

        # Mouse wheel support (works on Windows/Mac/Linux with Button-4/5 fallback)
        def _on_mousewheel(event):
            try:
                # X11 (Linux) uses Button-4/5 or event.num
                if hasattr(event, 'num') and event.num in (4, 5):
                    delta = 1 if event.num == 4 else -1
                    self._canvas.yview_scroll(-delta, 'units')
                else:
                    # Windows/Mac: event.delta is multiples of 120 (may be positive or negative)
                    try:
                        # Normalize to sign-int
                        d = int(event.delta / 120)
                    except Exception:
                        d = -1 if getattr(event, 'delta', 0) < 0 else 1
                    if d == 0:
                        d = -1 if getattr(event, 'delta', 0) < 0 else 1
                    self._canvas.yview_scroll(-d, 'units')
            except Exception:
                pass

        # Bind mousewheel only while pointer is over the canvas to avoid global scrolling
        try:
            # When the pointer enters the canvas, bind wheel events to the canvas widget
            def _on_enter(_e):
                try:
                    self._canvas.bind('<MouseWheel>', _on_mousewheel)
                    self._canvas.bind('<Button-4>', _on_mousewheel)
                    self._canvas.bind('<Button-5>', _on_mousewheel)
                except Exception:
                    pass

            def _on_leave(_e):
                try:
                    self._canvas.unbind('<MouseWheel>')
                    self._canvas.unbind('<Button-4>')
                    self._canvas.unbind('<Button-5>')
                except Exception:
                    pass

            self._canvas.bind('<Enter>', _on_enter)
            self._canvas.bind('<Leave>', _on_leave)
            # Also bind once so clicks/focus still scroll if already over canvas
            try:
                self._canvas.bind('<MouseWheel>', _on_mousewheel)
                self._canvas.bind('<Button-4>', _on_mousewheel)
                self._canvas.bind('<Button-5>', _on_mousewheel)
            except Exception:
                pass
        except Exception:
            # Fallback: attach global handlers (last resort)
            try:
                self._canvas.bind_all('<MouseWheel>', _on_mousewheel)
                self._canvas.bind_all('<Button-4>', _on_mousewheel)
                self._canvas.bind_all('<Button-5>', _on_mousewheel)
            except Exception:
                pass

        # Title (placed inside scrollable area)
        tk.Label(self.main_inner, text="Consciousness Field Lab",
             font=self.title_font, bg=self.bg_color, fg=self.accent_color)
        # store title label ref for resizing
        try:
            self.title_label = self.main_inner.winfo_children()[-1]
            self.title_label.pack(pady=10)
        except Exception:
            pass
        
        # HRV Section
        hrv_frame = tk.LabelFrame(self.main_inner, text="HRV Devices", padx=15, pady=10, bg=self.panel_color)
        hrv_frame.pack(fill="x", padx=20, pady=5)
        
        scan_btn = ttk.Button(hrv_frame, text="  Scan Devices", command=self.scan_devices, style='Accent.TButton', image=self._icons['scan'], compound=tk.LEFT)
        try:
            scan_btn.config(width=12)
        except Exception:
            pass
        self.scan_btn = scan_btn
        scan_btn.pack(pady=5)

        # Graph test button for generating synthetic HRV samples (for debugging/QA)
        try:
            self.hrv_graph_test_btn = ttk.Button(hrv_frame, text="Graph Test", command=self._toggle_hrv_graph_test)
            self.hrv_graph_test_btn.pack(pady=4)
        except Exception:
            self.hrv_graph_test_btn = None

        # Tooltip helper (attached later)
        
        self.device_list = tk.Frame(hrv_frame)
        self.device_list.pack(fill="both", expand=True)

        # HRV live stream display (recent samples)
        try:
            self.hrv_stream_box = scrolledtext.ScrolledText(hrv_frame, height=6, wrap=tk.WORD)
            self.hrv_stream_box.pack(fill='both', expand=False, pady=(6,0))
            self.hrv_stream_box.configure(state=tk.DISABLED)
        except Exception:
            self.hrv_stream_box = None
        # HRV plot area: prefer Matplotlib if available, otherwise fallback to simple canvas
        try:
            plot_frame = tk.Frame(hrv_frame, bg=self.bg_color)
            plot_frame.pack(fill='x', pady=(6,0))

            self._hrv_plot_paused = False
            self._hrv_history_len = 200

            if _MPL_AVAILABLE and Figure is not None:
                # Create a matplotlib Figure and embed it
                try:
                    fig = Figure(figsize=(6, 1.2), dpi=100)
                    ax = fig.add_subplot(111)
                    ax.set_ylim(0, 1)
                    ax.set_xlim(0, self._hrv_history_len)
                    ax.set_xlabel('Samples')
                    ax.set_ylabel('Coherence')
                    ax.grid(True, linestyle=':', linewidth=0.5)
                    self._hrv_fig = fig
                    self._hrv_ax = ax
                    self._hrv_line, = ax.plot([], [], color='#2c3e50', linewidth=1.5)
                    self._hrv_canvas = FigureCanvasTkAgg(fig, master=plot_frame)
                    self._hrv_canvas_widget = self._hrv_canvas.get_tk_widget()
                    self._hrv_canvas_widget.pack(side='left', fill='both', expand=True, padx=(0,6))
                    # For sparkline compatibility use hrv_spark_canvas==None (matplotlib used)
                    self.hrv_spark_canvas = None
                except Exception:
                    self._hrv_canvas = None
                    self._hrv_canvas_widget = None
                    self._hrv_fig = None
                    self._hrv_ax = None
                    self._hrv_line = None
            else:
                # Fallback simple canvas sparkline
                try:
                    self._hrv_canvas = None
                    self._hrv_canvas_widget = tk.Canvas(plot_frame, height=60, bg='#ffffff', bd=1, relief=tk.SUNKEN)
                    self._hrv_canvas_widget.pack(side='left', fill='x', expand=True, padx=(0,6))
                    # Expose a common name used by sparkline drawing
                    self.hrv_spark_canvas = self._hrv_canvas_widget
                except Exception:
                    self._hrv_canvas_widget = None

            # Controls for the HRV plot
            ctrl_frame = tk.Frame(plot_frame, bg=self.bg_color)
            ctrl_frame.pack(side='right', fill='y')

            self.hrv_pause_btn = ttk.Button(ctrl_frame, text='Pause', width=8, command=lambda: self._toggle_hrv_plot_pause())
            self.hrv_pause_btn.pack(padx=4, pady=2)

            tk.Label(ctrl_frame, text='History:').pack(padx=4)
            self.hrv_history_scale = tk.Scale(ctrl_frame, from_=50, to=1000, orient='vertical', showvalue=True, resolution=10, command=lambda v: self._set_hrv_history(int(float(v))))
            self.hrv_history_scale.set(self._hrv_history_len)
            self.hrv_history_scale.pack(padx=4, pady=2)

            self.hrv_export_csv_btn = ttk.Button(ctrl_frame, text='Export CSV', command=self._export_hrv_csv)
            self.hrv_export_csv_btn.pack(padx=4, pady=(8,2))
            self.hrv_export_png_btn = ttk.Button(ctrl_frame, text='Export PNG', command=self._export_hrv_png)
            self.hrv_export_png_btn.pack(padx=4, pady=2)

            # Label for latest coherence
            self.hrv_spark_label = tk.Label(ctrl_frame, text='Coh: --', width=12, bg=self.bg_color)
            self.hrv_spark_label.pack(padx=4, pady=(10,2))

        except Exception:
            self._hrv_canvas = None
            self._hrv_canvas_widget = None
            self._hrv_fig = None
            self._hrv_ax = None
            self._hrv_line = None
            self.hrv_pause_btn = None
            self.hrv_history_scale = None
            self.hrv_export_csv_btn = None
            self.hrv_export_png_btn = None
            self.hrv_spark_label = None
            self.hrv_spark_canvas = None
        
        # Stats Display
        stats_frame = tk.LabelFrame(self.main_inner, text="Live Statistics", padx=15, pady=10, bg=self.panel_color)
        stats_frame.pack(fill="x", padx=20, pady=5)
        
        self.mode_label = tk.Label(stats_frame, text="Mode: IDLE",
                 font=self.header_font, fg="#7f8c8d", bg=self.panel_color)
        self.mode_label.pack()
        
        self.stats_label = tk.Label(stats_frame, text="Waiting to start...",
                  font=self.stats_font, bg=self.panel_color)
        self.stats_label.pack(pady=5)
        
        self.effect_label = tk.Label(stats_frame, text="", font=self.small_font)
        self.effect_label.pack()
        
        self.coherence_label = tk.Label(stats_frame, text="", font=self.small_font)
        self.coherence_label.pack()
        
        # Participant Display (for group sessions)
        self.participant_frame = tk.LabelFrame(self.main_inner, text="Active Participants", padx=15, pady=10, bg=self.panel_color)
        # Do not pack yet; will be shown after status bar is created to avoid ordering issues
        
        self.participant_labels = {}
        
        # Session controls reside in the left sidebar so they remain visible without scrolling
        session_frame = tk.LabelFrame(self.sidebar, text="Session Controls", padx=12, pady=10, bg=self.panel_color)
        session_frame.pack(fill='x', pady=(0, 10))

        self.baseline_btn = ttk.Button(session_frame, text="Run Baseline", command=lambda: self.toggle_session("baseline"),
                                       style='Accent.TButton', image=self._icons['baseline'], compound=tk.LEFT)
        self.baseline_btn.pack(fill='x', pady=3)

        self.experiment_btn = ttk.Button(session_frame, text="Start Experiment", command=lambda: self.toggle_session("experiment"),
                                         style='Accent.TButton', image=self._icons['experiment'], compound=tk.LEFT)
        self.experiment_btn.pack(fill='x', pady=3)

        # Session duration presets (minutes)
        duration_row = tk.Frame(session_frame, bg=self.panel_color)
        duration_row.pack(fill='x', pady=(10, 4))
        tk.Label(duration_row, text="Duration:", bg=self.panel_color).pack(side='left')
        self.duration_var = tk.StringVar(value="5")
        presets = ["0.5", "1", "5", "10", "30", "60"]
        self.duration_menu = tk.OptionMenu(duration_row, self.duration_var, *presets)
        self.duration_menu.config(width=6)
        self.duration_menu.pack(side='left', padx=6)

        self.countdown_label = tk.Label(session_frame, text="Time left: --:--", anchor='w', bg=self.panel_color)
        self.countdown_label.pack(fill='x', pady=(6, 2))
        self.subject_info_label = tk.Label(session_frame, text="Subject: (none)", anchor='w', bg=self.panel_color)
        self.subject_info_label.pack(fill='x')

        # Quick Actions stacked vertically for accessibility
        action_frame = tk.LabelFrame(self.sidebar, text="Quick Actions", padx=12, pady=10, bg=self.panel_color)
        action_frame.pack(fill='x', pady=(0, 10))
        self.action_frame = action_frame

        self.mark_btn = ttk.Button(action_frame, text="Mark Intention", command=self.mark_intention, image=self._icons['group'], compound=tk.LEFT)
        self.mark_btn.pack(fill='x', pady=2)

        self.group_btn = ttk.Button(action_frame, text="Group Session", command=self.start_group_session, image=self._icons['group'], compound=tk.LEFT)
        self.group_btn.pack(fill='x', pady=2)

        self.toggle_bt_btn = ttk.Button(action_frame, text="Toggle Bluetooth", command=self.toggle_bluetooth, image=self._icons['bt'], compound=tk.LEFT)
        self.toggle_bt_btn.pack(fill='x', pady=2)

        self.seed_btn = ttk.Button(action_frame, text="Seed RNG (SDR)", command=self.seed_rng_from_sdr, image=self._icons['seed'], compound=tk.LEFT)
        self.seed_btn.pack(fill='x', pady=2)

        self.sdr_stream_btn = ttk.Button(action_frame, text="Start SDR Stream", command=self.toggle_sdr_stream)
        self.sdr_stream_btn.pack(fill='x', pady=2)

        try:
            self.spectral_check = tk.Checkbutton(action_frame, text="Spectral", variable=self._sdr_spectral_enabled_var,
                                                 command=lambda: self._on_spectral_toggle(), bg=self.panel_color)
            self.spectral_check.pack(fill='x', pady=2)
        except Exception:
            self.spectral_check = None

        self.import_baseline_btn = ttk.Button(action_frame, text="Import Baseline", command=self.import_baseline)
        self.import_baseline_btn.pack(fill='x', pady=(6, 2))

        self.compare_baseline_btn = ttk.Button(action_frame, text="Compare Baseline", command=self.compare_baseline)
        self.compare_baseline_btn.pack(fill='x', pady=2)

        ttk.Separator(action_frame, orient='horizontal').pack(fill='x', pady=(8, 6))

        self.self_admin_btn = ttk.Button(action_frame, text="Self-Admin", command=lambda: self.set_admin_mode('self'))
        self.self_admin_btn.pack(fill='x', pady=2)

        self.external_admin_btn = ttk.Button(action_frame, text="External Admin", command=self.enter_external_admin)
        self.external_admin_btn.pack(fill='x', pady=2)

        self.start_test_btn = ttk.Button(action_frame, text="Start Test (Self-Admin)", command=self.start_test_self_admin)
        self.start_test_btn.pack(fill='x', pady=2)

        self.end_test_btn = ttk.Button(action_frame, text="End Test", command=self.end_test)
        self.end_test_btn.pack(fill='x', pady=2)

        self.bt_debug_btn = ttk.Button(action_frame, text="BT Debug", command=self.bt_debug)
        self.bt_debug_btn.pack(fill='x', pady=(6, 2))

        ttk.Separator(action_frame, orient='horizontal').pack(fill='x', pady=(8, 6))

        # Provide quick access to export via sidebar
        self.export_btn = ttk.Button(action_frame, text="Export Session", command=self.export_session)
        self.export_btn.pack(fill='x', pady=2)

        # Small status indicators (inside scrollable area)
        status_frame = tk.Frame(self.main_inner, bg=self.bg_color)
        status_frame.pack(fill='x', padx=20, pady=(4,0))

        self.rng_led = tk.Label(status_frame, text=" RNG ", bg="#95a5a6", fg="white", relief=tk.RIDGE)
        self.rng_led.pack(side='left', padx=4)
        self.bt_led = tk.Label(status_frame, text=" BT ", bg="#95a5a6", fg="white", relief=tk.RIDGE)
        self.bt_led.pack(side='left', padx=4)
        self.sdr_led = tk.Label(status_frame, text=" SDR ", bg="#95a5a6", fg="white", relief=tk.RIDGE)
        self.sdr_led.pack(side='left', padx=4)

        # Initialize leds state
        self._set_led(self.rng_led, "off")
        self._set_led(self.bt_led, "off")
        self._set_led(self.sdr_led, "unknown")

        # SDR stream state
        self._sdr_streaming = False

        # Menu bar
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export Session", command=self.export_session)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Troubleshooting", command=self.show_troubleshooting)
        help_menu.add_command(label="About", command=lambda: messagebox.showinfo("About", "mindfield-core — GUI v1"))
        menubar.add_cascade(label="Help", menu=help_menu)
        # Admin Mode menu
        mode_menu = tk.Menu(menubar, tearoff=0)
        mode_menu.add_command(label="Switch to Self-Admin Mode", command=lambda: self.set_admin_mode('self'))
        mode_menu.add_command(label="Enter External Admin Mode", command=self.enter_external_admin)
        menubar.add_cascade(label="Mode", menu=mode_menu)

        self.root.config(menu=menubar)
        
        # Status Bar
        self.status_bar = tk.Label(self.root, text="Ready", bd=1, 
                                 relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Ensure participant frame is visible (placeholder) and placed before status bar
        try:
            if not self.participant_frame.winfo_ismapped():
                self.participant_frame.pack(fill="x", padx=20, pady=5, before=self.status_bar)
            if not self.participant_frame.winfo_children():
                tk.Label(self.participant_frame, text="No participants", fg="#95a5a6", bg=self.panel_color).pack()
        except Exception:
            pass

        # SDR status (kept outside the scrollable area so it's always visible)
        self.sdr_status_label = tk.Label(self.root, text="SDR: unknown", bd=1,
                 relief=tk.GROOVE, anchor=tk.W, bg=self.bg_color)
        self.sdr_status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # SDR current frequency / throughput display
        self._sdr_last_freq = None
        self._sdr_last_measured_freq = None
        self._sdr_last_bits_count = 0
        self.sdr_freq_label = tk.Label(self.root, text="SDR freq: --", bd=1,
                   relief=tk.GROOVE, anchor=tk.W, bg=self.bg_color)
        self.sdr_freq_label.pack(side=tk.BOTTOM, fill=tk.X)

        # Show onboarding on first run
        try:
            seen_flag = os.path.join(os.getcwd(), '.mindfield_seen')
            if not os.path.exists(seen_flag):
                # show onboarding dialog and create flag
                self.show_onboarding()
                try:
                    with open(seen_flag, 'w') as f:
                        f.write('seen')
                except Exception:
                    pass
        except Exception:
            pass

        # Tooltips
        try:
            self._attach_tooltips()
        except Exception:
            pass
        # Bind root resize to scale UI fonts/widgets (debounced)
        try:
            self._resize_after_id = None
            self.root.bind('<Configure>', self._on_root_config)
        except Exception:
            pass
        # Apply initial scaling and reflow immediately so layout is compact on startup
        try:
            self._apply_ui_scale()
        except Exception:
            pass
        try:
            self._reflow_action_buttons()
        except Exception:
            pass
        
    def scan_devices(self):
        self.status_bar.config(text="Scanning for HRV devices...")
        
        def scan_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                devices = loop.run_until_complete(self.hrv_manager.scan_devices())
                self.root.after(0, lambda: self.display_devices(devices))
            except Exception as e:
                msg = "Enable Bluetooth to scan" if "bluez" in str(e).lower() else f"Scan failed: {e}"
                self.root.after(0, lambda: self.show_error(msg))
                
        threading.Thread(target=scan_async, daemon=True).start()
        
    def display_devices(self, devices):
        # Clear previous
        for widget in self.device_list.winfo_children():
            widget.destroy()
        self.device_vars = []
        
        if not devices:
            tk.Label(self.device_list, text="No devices found", fg="#95a5a6").pack()
            self.status_bar.config(text="No HRV devices detected")
        else:
            self.status_bar.config(text=f"Found {len(devices)} device(s)")
            for dev in devices:
                var = tk.BooleanVar()
                cb = tk.Checkbutton(self.device_list, 
                                  text=f"{dev['name']} ({dev['address'][-5:]}) [{dev['rssi']}dB]",
                                  variable=var)
                cb.pack(anchor='w')
                self.device_vars.append((var, dev['address'], dev['name']))
                
    def start_group_session(self):
        selected = [(addr, name) for var, addr, name in self.device_vars if var.get()]
        if not selected:
            messagebox.showinfo("No Devices", "Select HRV devices first")
            return
            
        self.group_manager = GroupSessionManager(self.root, self.hrv_manager)
        assignments = self.group_manager.start_dialog(selected)
        
        if assignments:
            self.current_session_type = "group"
            self.setup_participant_display(assignments['participants'])
            
            # Connect devices
            self.hrv_manager.connect_devices(list(assignments['participants'].keys()))
            # If in external admin mode, show subject info (first participant)
            try:
                if getattr(self, 'admin_mode', 'external') == 'external':
                    first = next(iter(assignments['participants'].values()))
                    name = first.get('name') if isinstance(first, dict) else str(first)
                    try:
                        self.subject_info_label.config(text=f"Subject: {name}")
                    except Exception:
                        pass
            except Exception:
                pass

            self.toggle_session("experiment")

    def test_hrv_stream(self):
        """Check if selected HRV devices are transmitting RR/HR data.

        Connects to selected devices (if not already connected), waits up to
        `timeout` seconds for incoming data, and shows a summary dialog.
        """
        selected = [addr for var, addr, _ in self.device_vars if var.get()]
        if not selected:
            messagebox.showinfo("No Devices", "Select HRV devices first")
            return

        def worker():
            self.root.after(0, lambda: self.status_bar.config(text="Testing HRV streams..."))
            logger.info('HRV stream test started for: %s', selected)

            # Ensure devices are being monitored
            try:
                self.hrv_manager.connect_devices(selected)
            except Exception:
                logger.exception('Failed to start HRV connect')

            found = {}
            timeout = 12
            start = time.time()
            while time.time() - start < timeout:
                try:
                    data = self.hrv_manager.get_all_coherence()
                    for entry in data:
                        addr = entry.get('device')
                        if addr in selected:
                            # record latest sample
                            found.setdefault(addr, []).append(entry)
                    if found:
                        break
                except Exception:
                    logger.exception('Error reading HRV coherence')
                time.sleep(1)

            # Prepare result text
            if not found:
                txt = "No HRV data received within timeout. Ensure devices are connected and transmitting."
                logger.warning('HRV test: no data for %s', selected)
            else:
                lines = []
                for addr, samples in found.items():
                    s = samples[-1]
                    hr = s.get('heart_rate', '(n/a)')
                    coh = s.get('coherence', 0)
                    lines.append(f"{addr}: HR={hr} bpm, coherence={coh:.3f}, samples={len(samples)}")
                txt = "\n".join(lines)
                logger.info('HRV test results: %s', txt)

            def _show():
                try:
                    self.status_bar.config(text="HRV test complete")
                    dlg = tk.Toplevel(self.root)
                    dlg.title("HRV Stream Test Results")
                    dlg.geometry("540x240")
                    st = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
                    st.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
                    st.insert(tk.END, txt + "\n\nRaw samples (latest per device):\n")
                    for addr, samples in found.items():
                        st.insert(tk.END, f"--- {addr} ({len(samples)} samples) ---\n")
                        for s in samples[-5:]:
                            st.insert(tk.END, json.dumps(s) + "\n")
                    st.configure(state=tk.DISABLED)
                    tk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=6)
                except Exception:
                    messagebox.showinfo("HRV Test", txt)

            self.root.after(0, _show)

        threading.Thread(target=worker, daemon=True).start()

    def bt_debug(self):
        """Run bluetooth diagnostics (bluetoothctl show, rfkill list) with timeouts and show results."""
        def worker():
            self.root.after(0, lambda: self.status_bar.config(text="Running BT diagnostics..."))
            out_lines = []
            # If dbus/BlueZ available, try to list adapters and powered state
            if dbus is not None:
                try:
                    try:
                        system_bus = dbus.SystemBus()
                    except Exception:
                        system_bus = None
                    if system_bus is not None:
                        try:
                            manager = dbus.Interface(
                                system_bus.get_object('org.bluez', '/'),
                                'org.freedesktop.DBus.ObjectManager'
                            )
                            objs = manager.GetManagedObjects()
                            adapter_info = []
                            for path, interfaces in objs.items():
                                if 'org.bluez.Adapter1' in interfaces:
                                    props = interfaces.get('org.bluez.Adapter1', {})
                                    powered = props.get('Powered')
                                    name = props.get('Alias') or props.get('Address') or path
                                    adapter_info.append(f"{path}: powered={powered}, alias={name}")
                            if adapter_info:
                                out_lines.append(('bluez dbus adapters', '\n'.join(adapter_info)))
                        except Exception:
                            logger.exception('Error reading BlueZ adapters via dbus')
                except Exception:
                    logger.exception('BlueZ dbus diagnostic failed')
            try:
                # bluetoothctl show
                try:
                    res = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
                    out_lines.append(('bluetoothctl show', res.stdout or res.stderr))
                    logger.debug('bluetoothctl show: %s', res.stdout)
                except subprocess.TimeoutExpired:
                    out_lines.append(('bluetoothctl show', 'TIMED OUT'))
                    logger.warning('bluetoothctl show timed out')
                except FileNotFoundError:
                    out_lines.append(('bluetoothctl show', 'NOT FOUND'))
                    logger.debug('bluetoothctl not installed')

                # rfkill list bluetooth
                try:
                    res = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True, timeout=5)
                    out_lines.append(('rfkill list bluetooth', res.stdout or res.stderr))
                    logger.debug('rfkill output: %s', res.stdout)
                except subprocess.TimeoutExpired:
                    out_lines.append(('rfkill list bluetooth', 'TIMED OUT'))
                    logger.warning('rfkill list bluetooth timed out')
                except FileNotFoundError:
                    out_lines.append(('rfkill list bluetooth', 'NOT FOUND'))
                    logger.debug('rfkill not installed')

            except Exception:
                logger.exception('BT debug failed')

            # Add verify_connectivity summary
            try:
                v = self.verify_connectivity(do_ble_scan=False)
                summary = []
                for k in ('bluez', 'bluetoothctl', 'rfkill', 'ble_scan'):
                    summary.append(f"{k}: {v.get(k)}")
                out_lines.append(('connectivity summary', '\n'.join(summary)))
            except Exception:
                logger.exception('Failed to append connectivity summary')

            def _show():
                try:
                    dlg = tk.Toplevel(self.root)
                    dlg.title('BT Diagnostics')
                    dlg.geometry('700x420')
                    txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
                    txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
                    for title, content in out_lines:
                        txt.insert(tk.END, f"=== {title} ===\n")
                        txt.insert(tk.END, (content or '').strip() + "\n\n")
                    txt.configure(state=tk.DISABLED)
                    tk.Button(dlg, text='Close', command=dlg.destroy).pack(pady=6)
                except Exception:
                    messagebox.showinfo('BT Diagnostics', '\n'.join([f"{t}: {c}" for t, c in out_lines]))

            self.root.after(0, _show)

        threading.Thread(target=worker, daemon=True).start()
            
    def setup_participant_display(self, participants):
        # Show participant frame
        self.participant_frame.pack(fill="x", padx=20, pady=5, before=self.status_bar)
        
        # Clear old labels
        for widget in self.participant_frame.winfo_children():
            widget.destroy()
            
        # Create label for each participant
        for addr, info in participants.items():
            frame = tk.Frame(self.participant_frame)
            frame.pack(fill="x", pady=2)
            
            name_label = tk.Label(frame, text=f"{info['name']} ({info['role']}): ",
                                width=20, anchor="w")
            name_label.pack(side="left")
            
            coherence_label = tk.Label(frame, text="-- waiting --", width=15)
            coherence_label.pack(side="left")
            
            self.participant_labels[addr] = coherence_label
            
    def toggle_session(self, mode):
        if not self.running:
            # If starting an experiment and no baseline imported yet, prompt user to import
            if mode == "experiment" and not self.rng_collector.baseline_bits:
                try:
                    want = messagebox.askyesno("Import Baseline", "No baseline data is currently imported. Import a baseline file now?")
                    if want:
                        # reuse existing import dialog
                        self.import_baseline()
                except Exception:
                    pass
            # For individual sessions, connect selected devices
            if self.current_session_type == "individual":
                selected = [addr for var, addr, _ in self.device_vars if var.get()]
                selected_names = [name for var, addr, name in self.device_vars if var.get()]
                if selected:
                    self.hrv_manager.connect_devices(selected)
                    self.status_bar.config(text=f"Connected to {len(selected)} device(s)")
                    # If in external admin mode, show the subject name
                    try:
                        if getattr(self, 'admin_mode', 'external') == 'external' and selected_names:
                            self.subject_info_label.config(text=f"Subject: {selected_names[0]}")
                    except Exception:
                        pass
            
            # Start RNG collection
            self.running = True
            self.rng_collector.start(mode)

            # Indicate RNG is active
            try:
                self._set_led(self.rng_led, 'on')
            except Exception:
                pass

            # Set session end time from duration entry (minutes)
            try:
                mins = float(self.duration_var.get())
                if mins <= 0:
                    raise ValueError()
            except Exception:
                mins = 5.0
            self.session_end_time = time.time() + mins * 60
            self.status_bar.config(text=f"Session started ({mode}), will stop in {int(mins)} min")
            
            # Update UI
            if mode == "baseline":
                self.mode_label.config(text="Mode: BASELINE", fg="#9b59b6")
                self.baseline_btn.config(text="Stop Baseline")
                self.experiment_btn.config(state="disabled")
            else:
                self.mode_label.config(text="Mode: EXPERIMENT", fg="#27ae60")
                self.experiment_btn.config(text="Stop Experiment")
                self.baseline_btn.config(state="disabled")
                
        else:
            self.stop_session()
            
    def stop_session(self):
        self.running = False
        self.rng_collector.stop()
        # Clear session end marker
        self.session_end_time = None
        
        # Reset UI
        self.mode_label.config(text="Mode: IDLE", fg="#7f8c8d")
        self.baseline_btn.config(text="Run Baseline", state="normal")
        self.experiment_btn.config(text="Start Experiment", state="normal")
        try:
            self._set_led(self.rng_led, 'off')
        except Exception:
            pass
        
        # Auto-save prompt
        if messagebox.askyesno("Save Data", "Save session data?"):
            self.export_session()

        # After session ends, if a baseline exists, compute and show final comparison
        try:
            comp = self.rng_collector.get_baseline_comparison()
            if comp:
                txt = (f"Baseline mean: {comp['baseline_mean']:.6f}\n"
                       f"Experiment mean: {comp['experiment_mean']:.6f}\n"
                       f"Effect percent: {comp['effect_percent']:+.4f}%\n"
                       f"Baseline bits: {comp['baseline_bits']}, Experiment bits: {comp['experiment_bits']}")
                # Show summary and offer to save comparison
                messagebox.showinfo('Final Comparison', txt)
                try:
                    save = messagebox.askyesno('Save Comparison', 'Save comparison results to file?')
                except Exception:
                    save = False
                if save:
                    path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON','*.json')], initialfile='comparison.json')
                    if path:
                        try:
                            with open(path, 'w') as f:
                                json.dump({'comparison': comp, 'timestamp': datetime.now().isoformat()}, f, indent=2)
                            self._audit_event('save-comparison', {'path': path, 'comp': comp})
                            messagebox.showinfo('Saved', f'Comparison saved to {path}')
                        except Exception as e:
                            messagebox.showwarning('Save failed', f'Could not save comparison: {e}')
        except Exception:
            pass
            
    def mark_intention(self):
        if not self.running or self.rng_collector.mode == "baseline":
            messagebox.showinfo("Info", "Run an experiment to mark intentions")
            return
        # Prompt user for the intent type
        intent = self._prompt_intent()
        if not intent:
            # cancelled
            return

        coherence_data = self.hrv_manager.get_all_coherence()
        try:
            self.rng_collector.mark_event("intention", coherence_data, meta={'intent': intent})
        except TypeError:
            # fallback if RNGCollector older signature
            self.rng_collector.mark_event("intention", coherence_data)
        self.status_bar.config(text=f"Marked intention '{intent}' at bit {self.rng_collector.get_stats()['count']}")

    def _prompt_intent(self):
        """Show a modal dialog to choose an intention label and return it (or None if cancelled)."""
        intents = [
            "Send Calm / Relaxation",
            "Increase Focus / Attention",
            "Increase Coherence / Synchrony",
            "Lower Heart Rate / Relax",
            "Send Healing / Wellbeing",
            "Improve Sleep / Rest",
            "Generate Random Intention",
            "Other..."
        ]

        dlg = tk.Toplevel(self.root)
        dlg.title("Select Intention")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("420x200")

        tk.Label(dlg, text="Choose an intention to mark:", font=self.header_font).pack(pady=(12,6))

        sel_var = tk.StringVar(value=intents[0])
        opt = ttk.Combobox(dlg, values=intents, textvariable=sel_var, state='readonly')
        opt.pack(fill='x', padx=20)

        other_var = tk.StringVar()
        other_entry = tk.Entry(dlg, textvariable=other_var)
        other_entry.pack(fill='x', padx=20, pady=(6,0))
        other_entry.insert(0, "(optional: type custom intent here)")

        result = {'val': None}

        def _on_ok():
            choice = sel_var.get()
            if choice == 'Other...':
                val = other_var.get().strip()
                if not val or val.startswith('('):
                    messagebox.showwarning('Input required', 'Please enter a custom intent label.')
                    return
                result['val'] = val
            else:
                result['val'] = choice
            dlg.destroy()

        def _on_cancel():
            dlg.destroy()

        btnf = tk.Frame(dlg)
        btnf.pack(fill='x', pady=12)
        tk.Button(btnf, text='OK', command=_on_ok).pack(side='right', padx=12)
        tk.Button(btnf, text='Cancel', command=_on_cancel).pack(side='right')

        self.root.wait_window(dlg)
        return result['val']
        
    def update_loop(self):
        if self.running:
            # Get RNG stats
            stats = self.rng_collector.get_stats()
            
            # Update main stats
            self.stats_label.config(
                text=f"Mean: {stats['mean']:.4f} | Z-score: {stats['z_score']:+.3f} | Bits: {stats['count']:,}"
            )
            
            # Color code z-score
            if abs(stats['z_score']) > 3:
                self.stats_label.config(fg="#e74c3c")  # Red for high significance
            elif abs(stats['z_score']) > 2:
                self.stats_label.config(fg="#f39c12")  # Orange for significant
            else:
                self.stats_label.config(fg="black")
            
            # Update effect size if available
            comparison = self.rng_collector.get_baseline_comparison()
            if comparison:
                self.effect_label.config(
                    text=f"Effect: {comparison['effect_percent']:+.2f}% from baseline",
                    fg="#e74c3c" if abs(comparison['effect_percent']) > 1 else "#7f8c8d"
                )
            
            # Update coherence
            coherence_data = self.hrv_manager.get_all_coherence()
            if coherence_data:
                # Group session - show individual coherence
                if self.current_session_type == "group" and hasattr(self.hrv_manager, 'device_names'):
                    for data in coherence_data:
                        addr = data.get('device')
                        if addr in self.participant_labels:
                            self.participant_labels[addr].config(
                                text=f"Coherence: {data['coherence']:.3f}"
                            )
                
                # Overall coherence
                avg_coherence = sum(d['coherence'] for d in coherence_data) / len(coherence_data)
                device_count = len(set(d['device'] for d in coherence_data))
                self.coherence_label.config(
                    text=f"Avg Coherence: {avg_coherence:.3f} ({device_count} device{'s' if device_count != 1 else ''})"
                )
                
                # Auto-mark high coherence
                if avg_coherence > 0.8 and self.rng_collector.mode == "experiment":
                    self.rng_collector.mark_event("high_coherence", coherence_data)
            
            # Check session time limit
            if hasattr(self, 'session_end_time') and self.session_end_time:
                remaining = int(self.session_end_time - time.time())
                if remaining <= 0:
                    # Time's up
                    if self.running:
                        self.stop_session()
                        self.status_bar.config(text="Session ended (time limit)")
                else:
                    mins = remaining // 60
                    secs = remaining % 60
                    try:
                        self.countdown_label.config(text=f"Time left: {mins:02d}:{secs:02d}")
                    except Exception:
                        pass
                    
        # Refresh SDR frequency/throughput display
        try:
            if getattr(self, '_sdr_streaming', False):
                freq = getattr(self, '_sdr_last_freq', None)
                if freq:
                    try:
                        mhz = float(freq) / 1e6
                        freq_text = f"SDR center: {mhz:.3f} MHz"
                    except Exception:
                        freq_text = f"SDR center: {freq}"
                else:
                    freq_text = "SDR center: --"

                try:
                    now = time.time()
                    last_t = getattr(self, '_sdr_throughput_last_time', None)
                    last_count = getattr(self, '_sdr_last_bits_count', None)
                    if last_t is None or last_count is None:
                        self.sdr_freq_label.config(text=freq_text)
                        # Initialize snapshots
                        self._sdr_last_bits_count = len(self.rng_collector.bits)
                        self._sdr_throughput_last_time = now
                    else:
                        delta_bits = max(0, len(self.rng_collector.bits) - last_count)
                        delta_t = max(0.001, now - last_t)
                        rate = delta_bits / delta_t
                        # Prefer measured peak (smoothed) if available
                        measured = getattr(self, '_sdr_last_measured_freq', None)
                        if measured:
                            # Exponential moving average for smoothing
                            if self._sdr_measured_ema is None:
                                self._sdr_measured_ema = float(measured)
                            else:
                                try:
                                    self._sdr_measured_ema = (self._sdr_ema_alpha * float(measured) +
                                                            (1.0 - self._sdr_ema_alpha) * float(self._sdr_measured_ema))
                                except Exception:
                                    pass
                            try:
                                display_mhz = float(self._sdr_measured_ema) / 1e6
                                freq_label = f"SDR peak: {display_mhz:.3f} MHz"
                            except Exception:
                                freq_label = freq_text
                            self.sdr_freq_label.config(text=f"{freq_label} | {rate:.1f} bits/s")
                        else:
                            self.sdr_freq_label.config(text=f"{freq_text} | {rate:.1f} bits/s")
                        self._sdr_last_bits_count = len(self.rng_collector.bits)
                        self._sdr_throughput_last_time = now
                except Exception:
                    try:
                        self.sdr_freq_label.config(text=freq_text)
                    except Exception:
                        pass
            else:
                try:
                    self.sdr_freq_label.config(text="SDR freq: --")
                except Exception:
                    pass
        except Exception:
            pass

        # Schedule next update
        # Re-enable SDR UI after backoff expires
        try:
            if getattr(self, '_sdr_disabled_until', 0) and time.time() >= getattr(self, '_sdr_disabled_until', 0):
                try:
                    # Reset counters and re-enable button
                    self._sdr_fail_count = 0
                    self._sdr_disabled_until = 0
                    try:
                        self.sdr_stream_btn.config(text="  Start SDR Stream")
                        try:
                            self.sdr_stream_btn.state(['!disabled'])
                        except Exception:
                            self.sdr_stream_btn.config(state='normal')
                    except Exception:
                        pass
                    self.status_bar.config(text="SDR controls re-enabled")
                except Exception:
                    pass
        except Exception:
            pass

        self.root.after(100, self.update_loop)
        
    def export_session(self):
        if not self.rng_collector.bits and not self.rng_collector.baseline_bits:
            messagebox.showinfo("No Data", "No session data to export")
            return
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"mindfield_{self.rng_collector.mode}_{timestamp}.csv"
        
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("JSON files", "*.json")],
            initialfile=filename
        )
        
        if not filepath:
            return
            
        stats = self.rng_collector.get_stats()
        comparison = self.rng_collector.get_baseline_comparison()
        
        if filepath.endswith('.json'):
            # JSON export with full data
            data = {
                'session_info': {
                    'timestamp': datetime.now().isoformat(),
                    'mode': self.rng_collector.mode,
                    'duration_seconds': stats['count'] * 0.01,
                    'type': self.current_session_type
                },
                'statistics': stats,
                'comparison': comparison,
                # Respect admin mode: if in self-admin, don't include raw bits or detailed markers
                'markers': (self.rng_collector.markers if getattr(self, 'admin_mode', 'external') == 'external' else [{'count': len(self.rng_collector.markers)}]),
                'raw_bits': (list(self.rng_collector.bits)[-10000:] if getattr(self, 'admin_mode', 'external') == 'external' else None),
                'hrv_snapshots': (list(self.rng_collector.hrv_snapshots) if getattr(self, 'admin_mode', 'external') == 'external' else None)
            }
            
            # Add group session data if applicable
            if self.current_session_type == "group" and self.group_manager:
                data['group_info'] = self.group_manager.device_assignments
                
            with open(filepath, 'w') as f:
                json.dump(data, f, indent=2)
        else:
            # CSV export summary
            with open(filepath, 'w', newline='') as f:
                writer = csv.writer(f)
                
                # Header info
                writer.writerow(['Session Report - mindfield-core'])
                writer.writerow(['Timestamp', datetime.now()])
                writer.writerow(['Mode', self.rng_collector.mode])
                writer.writerow(['Type', self.current_session_type])
                writer.writerow([])
                
                # Statistics
                writer.writerow(['Statistics'])
                writer.writerow(['Mean', stats['mean']])
                writer.writerow(['Z-score', stats['z_score']])
                writer.writerow(['Total Bits', stats['count']])
                writer.writerow(['Markers', stats['markers']])
                
                if comparison:
                    writer.writerow([])
                    writer.writerow(['Baseline Comparison'])
                    writer.writerow(['Baseline Mean', comparison['baseline_mean']])
                    writer.writerow(['Experiment Mean', comparison['experiment_mean']])
                    writer.writerow(['Effect Size', f"{comparison['effect_percent']:.2f}%"])
                    
                # Markers
                if self.rng_collector.markers:
                    writer.writerow([])
                    writer.writerow(['Event Markers'])
                    writer.writerow(['Time', 'Event', 'Bit Index'])
                    # Respect admin mode: redact detailed markers if self-admin
                    if getattr(self, 'admin_mode', 'external') == 'external':
                        for m in self.rng_collector.markers:
                            writer.writerow([m.get('timestamp'), m.get('event'), m.get('bit_index')])
                    else:
                        writer.writerow(['(redacted in self-admin mode)', '', ''])
                # HRV snapshots
                if getattr(self.rng_collector, 'hrv_snapshots', None):
                    writer.writerow([])
                    writer.writerow(['HRV Snapshots'])
                    writer.writerow(['timestamp', 'device', 'heart_rate', 'coherence', 'bit_index', 'rr_intervals'])
                    if getattr(self, 'admin_mode', 'external') == 'external':
                        for s in self.rng_collector.hrv_snapshots:
                            writer.writerow([s.get('timestamp'), s.get('device'), s.get('heart_rate'), s.get('coherence'), s.get('bit_index'), json.dumps(s.get('rr_intervals'))])
                    else:
                        writer.writerow(['(redacted in self-admin mode)'])
                        
        # Save group metadata if group session
        if self.current_session_type == "group" and self.group_manager:
            self.group_manager.save_session_metadata(filepath)
        # Audit export action
        try:
            who = getpass.getuser()
            self._audit_event('export-session', {'user': who, 'filepath': filepath, 'mode': getattr(self, 'admin_mode', 'external')})
        except Exception:
            pass
        messagebox.showinfo("Exported", f"Session data saved to {filepath}")
        
    def show_error(self, message):
        messagebox.showerror("Error", message)
        self.status_bar.config(text="Error occurred")

    def _set_led(self, label_widget, state: str):
        """Set a small status LED label's background based on state.

        state: 'on' | 'off' | 'unknown'
        """
        try:
            if state == "on":
                label_widget.config(bg="#27ae60")
            elif state == "off":
                label_widget.config(bg="#e74c3c")
            else:
                label_widget.config(bg="#95a5a6")
        except Exception:
            pass

    def _run_with_possible_privilege(self, cmd, timeout=None):
        """Run `cmd` (a list) and if it fails due to permission, ask the user
        on the main thread to authorize and re-run via `pkexec` (or `sudo` fallback).

        This helper may block the calling thread while waiting for the main
        thread to perform the privileged call; it's safe to call from a
        background worker.
        Returns a subprocess.CompletedProcess-like object.
        """
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except Exception as e:
            # Could not run even the non-privileged command
            logger.exception('Failed to run command: %s', cmd)
            try:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(e))
            except Exception:
                return None

        # If command succeeded, return
        if res.returncode == 0:
            logger.debug('Command succeeded: %s', cmd)
            return res

        out = (res.stderr or "") + (res.stdout or "")
        low = out.lower()

        # Heuristic: permission errors include 'permission', 'denied', 'not authorized'
        if any(x in low for x in ("permission", "denied", "not authorized", "authorization")):
            q = Queue()
            logger.debug('Permission-like error detected for cmd %s: %s', cmd, out)

            def _ask_and_run():
                try:
                    proceed = messagebox.askyesno("Authorization required",
                                                   f"Administrator privileges are required to run:\n{' '.join(cmd)}\n\nAllow? ")
                except Exception:
                    proceed = False

                if not proceed:
                    logger.info('User denied privilege elevation for: %s', cmd)
                    q.put(subprocess.CompletedProcess(cmd, 126, stdout="", stderr="user denied"))
                    return

                # Try pkexec first
                try:
                    pcmd = ['pkexec'] + cmd
                    logger.debug('Attempting pkexec for: %s', cmd)
                    r2 = subprocess.run(pcmd, capture_output=True, text=True, timeout=timeout)
                    logger.debug('pkexec result: %s', getattr(r2, 'returncode', None))
                    q.put(r2)
                    return
                except FileNotFoundError:
                    logger.debug('pkexec not found; will try sudo')
                except Exception as e:
                    logger.exception('pkexec execution failed')
                    # Put the error result and continue to sudo fallback
                    q.put(subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(e)))
                    return

                # Fallback to sudo
                try:
                    scmd = ['sudo'] + cmd
                    logger.debug('Attempting sudo for: %s', cmd)
                    r3 = subprocess.run(scmd, capture_output=True, text=True, timeout=timeout)
                    logger.debug('sudo result: %s', getattr(r3, 'returncode', None))
                    q.put(r3)
                    return
                except Exception as e:
                    logger.exception('sudo execution failed')
                    q.put(subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(e)))

            # Schedule the authorization on the main thread so messagebox and polkit dialogs can appear
            try:
                self.root.after(0, _ask_and_run)
                return q.get()
            except Exception as e:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(e))

        return res

    def enter_external_admin(self):
        """Enter External Admin mode (no password required)."""
        try:
            self.set_admin_mode('external')
            messagebox.showinfo("External Admin", "External Admin mode enabled")
        except Exception as e:
            messagebox.showerror("Error", f"Could not enable external admin: {e}")

    def set_admin_mode(self, mode: str):
        """Set admin mode. mode is 'self' or 'external'. Adjust UI and data visibility accordingly."""
        try:
            mode = mode.lower()
            if mode not in ('self', 'external'):
                raise ValueError('mode must be self or external')
            self.admin_mode = mode

            # Update title/status to show current mode
            self.root.title(f"mindfield-core [{'Self-Admin' if mode=='self' else 'External Admin'}]")
            self.status_bar.config(text=f"Mode: {'Self-Admin (limited view)' if mode=='self' else 'External Admin (full view)'}")

            # Audit the admin switch
            try:
                who = getpass.getuser()
                self._audit_event('admin-switch', {'mode': mode, 'user': who})
            except Exception:
                pass

            # When in self-admin, minimize UI to timer-only view
            if mode == 'self':
                try:
                    # Disable action buttons
                    for btn in (getattr(self, 'baseline_btn', None), getattr(self, 'experiment_btn', None),
                                getattr(self, 'mark_btn', None), getattr(self, 'group_btn', None),
                                getattr(self, 'export_btn', None), getattr(self, 'toggle_bt_btn', None),
                                getattr(self, 'seed_btn', None), getattr(self, 'scan_btn', None)):
                        if btn is not None:
                            try:
                                btn.state(['disabled'])
                            except Exception:
                                try:
                                    btn.config(state='disabled')
                                except Exception:
                                    pass
                    # For Self-Admin, disable controls but keep layout stable and visible
                    # This avoids layout shifts on small screens and keeps status context
                    try:
                        # Mark subject info as hidden, but keep the widget in place
                        self.subject_info_label.config(text="Subject: (hidden)")
                    except Exception:
                        pass
                except Exception:
                    pass

            else:
                # External admin — restore UI and enable buttons
                try:
                    for btn in (getattr(self, 'baseline_btn', None), getattr(self, 'experiment_btn', None),
                                getattr(self, 'mark_btn', None), getattr(self, 'group_btn', None),
                                getattr(self, 'export_btn', None), getattr(self, 'toggle_bt_btn', None),
                                getattr(self, 'seed_btn', None), getattr(self, 'scan_btn', None)):
                        if btn is not None:
                            try:
                                btn.state(['!disabled'])
                            except Exception:
                                try:
                                    btn.config(state='normal')
                                except Exception:
                                    pass

                    # Restore stats labels
                    try:
                        # If they were removed, re-pack/place
                        self.stats_label.pack(pady=5)
                    except Exception:
                        pass
                    try:
                        self.effect_label.pack()
                    except Exception:
                        pass
                    try:
                        self.coherence_label.pack()
                    except Exception:
                        pass

                    # Restore participant frame if participants exist
                    try:
                        if getattr(self, 'participant_labels', {}):
                            self.participant_frame.pack(fill="x", padx=20, pady=5, before=self.status_bar)
                    except Exception:
                        pass

                    # Subject info label will be updated when a session is started
                    try:
                        self.subject_info_label.config(text="Subject: (none)")
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Error", f"Could not set admin mode: {e}")
        finally:
            try:
                self._update_admin_buttons()
            except Exception:
                pass

    def _update_admin_buttons(self):
        """Update the admin mode buttons' labels to reflect current selection."""
        try:
            mode = getattr(self, 'admin_mode', 'external')
            if getattr(self, 'self_admin_btn', None) is not None:
                if mode == 'self':
                    self.self_admin_btn.config(text="Self-Admin ✓")
                else:
                    self.self_admin_btn.config(text="Self-Admin")
            if getattr(self, 'external_admin_btn', None) is not None:
                if mode == 'external':
                    self.external_admin_btn.config(text="External Admin ✓")
                else:
                    self.external_admin_btn.config(text="External Admin")
        except Exception:
            pass

    def _audit_event(self, event_type: str, details: dict):
        """Append an audit event to `audit.log` in the repo root.

        event_type: short string
        details: mapping
        """
        try:
            root = pathlib.Path(__file__).resolve().parent
            logp = root / 'audit.log'
            ts = datetime.utcnow().isoformat() + 'Z'
            who = details.get('user') or getpass.getuser()
            entry = {'timestamp': ts, 'event': event_type, 'user': who, 'details': details}

            # Ensure the audit log exists and has restrictive permissions (owner read/write only).
            # Write the entry, then attempt to chmod to 0o600 to limit access.
            try:
                # Open with append mode; create if needed
                with open(logp, 'a') as f:
                    f.write(json.dumps(entry) + "\n")
                try:
                    # Set restrictive permissions; ignore if not permitted
                    os.chmod(logp, 0o600)
                except Exception:
                    pass
            except Exception:
                # If writing/appending failed, ignore silently to avoid blocking UI
                pass
        except Exception:
            pass

    def start_test_self_admin(self):
        """One-click flow: switch to Self-Admin, start an experiment session, and audit the action."""
        try:
            # Switch to self-admin UI
            self.set_admin_mode('self')

            # Start an experiment session if not running
            if not self.running:
                # For individual mode, ensure selected devices are connected as usual
                # toggle_session will set up times and start RNG collection
                self.toggle_session('experiment')

            # Audit
            try:
                who = getpass.getuser()
                self._audit_event('start-test-self-admin', {'user': who})
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror('Error', f'Could not start test: {e}')

    def end_test(self):
        """End the current test/session if running. Audits the action."""
        try:
            if self.running:
                self.stop_session()
                try:
                    who = getpass.getuser()
                    self._audit_event('end-test', {'user': who})
                except Exception:
                    pass
                messagebox.showinfo('Test Ended', 'The current test has been stopped.')
            else:
                messagebox.showinfo('No Test', 'No test is currently running.')
        except Exception as e:
            messagebox.showerror('Error', f'Could not end test: {e}')

    def show_troubleshooting(self):
        """Open a small dialog showing polkit and udev guidance for Bluetooth and SDR access."""
        try:
            polkit_path = "POLKIT_RULES.md"
            udev_path = "udev/52-rtl-sdr.rules"
            sections = []

            try:
                with open(polkit_path, 'r') as f:
                    sections.append((polkit_path, f.read()))
            except Exception:
                sections.append((polkit_path, "(not found) - see repository POLKIT_RULES.md"))

            try:
                with open(udev_path, 'r') as f:
                    sections.append((udev_path, f.read()))
            except Exception:
                sections.append((udev_path, "(not found) - see repository udev/52-rtl-sdr.rules"))

            dlg = tk.Toplevel(self.root)
            dlg.title("Troubleshooting & Setup")
            dlg.geometry("700x500")
            txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
            txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

            for path, content in sections:
                txt.insert(tk.END, f"=== {path} ===\n")
                txt.insert(tk.END, content + "\n\n")

            txt.configure(state=tk.DISABLED)

            btn_frame = tk.Frame(dlg)
            btn_frame.pack(fill=tk.X, pady=(0,8))

            def _copy_all():
                try:
                    self.root.clipboard_clear()
                    self.root.clipboard_append('\n'.join([c for _, c in sections]))
                    messagebox.showinfo("Copied", "Troubleshooting text copied to clipboard")
                except Exception as e:
                    messagebox.showwarning("Copy failed", str(e))

            tk.Button(btn_frame, text="Copy To Clipboard", command=_copy_all).pack(side='left', padx=6)
            tk.Button(btn_frame, text="Apply Driver Fixes", command=lambda: threading.Thread(target=self.run_driver_fix, daemon=True).start()).pack(side='left', padx=6)
            tk.Button(btn_frame, text="Run rtl_test (root)", command=lambda: threading.Thread(target=self.run_rtl_test_as_root, daemon=True).start()).pack(side='left', padx=6)
            tk.Button(btn_frame, text="Undo Driver Fixes", command=lambda: threading.Thread(target=self.revert_driver_fix, daemon=True).start()).pack(side='left', padx=6)
            tk.Button(btn_frame, text="Close", command=dlg.destroy).pack(side='right', padx=6)

        except Exception as e:
            messagebox.showerror("Error", f"Could not open troubleshooting dialog: {e}")

    def show_onboarding(self):
        """Show onboarding dialog with quick checks for SDR and Bluetooth."""
        try:
            dlg = tk.Toplevel(self.root)
            dlg.title("Welcome to mindfield-core — Onboarding")
            dlg.geometry("620x360")

            header = tk.Label(dlg, text="Welcome — Quick Setup", font=self.header_font)
            header.pack(pady=(10,6))

            info = tk.Label(dlg, text=("This assistant will check for RTL-SDR availability and Bluetooth access. "
                                        "You can open Troubleshooting for polkit/udev instructions."), wraplength=580)
            info.pack(padx=10)

            frame = tk.Frame(dlg)
            frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

            self._onboard_sdr_label = tk.Label(frame, text="SDR: unknown", width=30, anchor='w')
            self._onboard_sdr_label.grid(row=0, column=0, sticky='w', pady=6)

            self._onboard_bt_label = tk.Label(frame, text="Bluetooth: unknown", width=30, anchor='w')
            self._onboard_bt_label.grid(row=1, column=0, sticky='w', pady=6)

            btns = tk.Frame(dlg)
            btns.pack(fill=tk.X, pady=(6,10))

            def _run_checks():
                def _worker():
                    try:
                        from sdr_rng import is_sdr_available
                        s = is_sdr_available()
                    except Exception:
                        s = False

                    # Use the new verify_connectivity helper for a more complete check
                    try:
                        bt_stats = self.verify_connectivity(do_ble_scan=False)
                    except Exception:
                        bt_stats = {'bluez': None, 'bluetoothctl': None, 'rfkill': None, 'ble_scan': None, 'ok': False}

                    self._onboard_sdr_label.config(text=f"SDR: {'available' if s else 'not available'}")
                    # Prefer BlueZ result if present
                    bt_text = bt_stats.get('bluez') or bt_stats.get('bluetoothctl') or bt_stats.get('rfkill')
                    if bt_text is True:
                        bt_text = 'unblocked'
                    if bt_text is False:
                        bt_text = 'blocked'
                    self._onboard_bt_label.config(text=f"Bluetooth: {bt_text if bt_text else 'unknown'}")
                    # Update BT LED
                    try:
                        self._set_led(self.bt_led, 'on' if bt_stats.get('ok') else 'off')
                    except Exception:
                        pass

                threading.Thread(target=_worker, daemon=True).start()

            tk.Button(btns, text="Run Checks", command=_run_checks).pack(side='left', padx=6)
            tk.Button(btns, text="Troubleshooting", command=self.show_troubleshooting).pack(side='left', padx=6)

            def _dont_show():
                try:
                    seen_flag = os.path.join(os.getcwd(), '.mindfield_seen')
                    with open(seen_flag, 'w') as f:
                        f.write('seen')
                except Exception:
                    pass
                dlg.destroy()

            tk.Button(btns, text="Don't show again", command=_dont_show).pack(side='right', padx=6)
            tk.Button(btns, text="Close", command=dlg.destroy).pack(side='right', padx=6)

        except Exception as e:
            print(f"Onboarding failed: {e}")

    def _attach_tooltips(self):
        """Attach small tooltips to key widgets."""
        class _ToolTip:
            def __init__(self, widget, text):
                self.widget = widget
                self.text = text
                self.tip = None
                widget.bind("<Enter>", self.show)
                widget.bind("<Leave>", self.hide)

            def show(self, _ev=None):
                try:
                    if self.tip:
                        return
                    x = self.widget.winfo_rootx() + 20
                    y = self.widget.winfo_rooty() + self.widget.winfo_height() + 10
                    self.tip = tk.Toplevel(self.widget)
                    self.tip.wm_overrideredirect(True)
                    self.tip.wm_geometry(f"+{x}+{y}")
                    lbl = tk.Label(self.tip, text=self.text, bg="#222", fg="white", bd=1, padx=6, pady=3)
                    lbl.pack()
                except Exception:
                    pass

            def hide(self, _ev=None):
                try:
                    if self.tip:
                        self.tip.destroy()
                        self.tip = None
                except Exception:
                    pass

        tips = [
            (self.baseline_btn, "Run a baseline session (no intentions)."),
            (self.experiment_btn, "Start an experiment session to record intentions."),
            (self.toggle_bt_btn, "Toggle system Bluetooth (BlueZ/rfkill fallback)."),
            (self.bt_debug_btn, "Run quick bluetoothctl/rfkill diagnostics."),
            (self.seed_btn, "Seed RNG from online quantum source (ANU QRNG) with SDR/software fallback."),
            (self.import_baseline_btn, "Import a saved baseline dataset for comparison."),
            (self.compare_baseline_btn, "Compare the current session against the imported baseline."),
            (self.export_btn, "Export current session data to CSV or JSON."),
            # HRV diagnostics handled inside HRV panel (Graph Test)
            (self.scan_btn, "Scan for HRV BLE devices nearby."),
        ]

        for w, t in tips:
            try:
                _ToolTip(w, t)
            except Exception:
                pass

        # Ensure action buttons are laid out to fit initial size
        try:
            self._reflow_action_buttons()
        except Exception:
            pass

        # Reflow when the main inner frame changes size
        try:
            self.main_inner.bind('<Configure>', lambda e: self._reflow_action_buttons(e))
        except Exception:
            pass

    def run_driver_fix(self):
        """Unload DVB kernel modules and optionally install udev/blacklist rules.

        This will request privilege escalation when needed via `_run_with_possible_privilege`.
        It writes temporary files into the current working directory then moves them into place.
        """
        try:
            ok = messagebox.askyesno('Driver Fix',
                                     'This will unload kernel modules that may conflict with RTL-SDR and optionally install udev and modprobe blacklist files to make the change persistent.\n\nContinue?')
            if not ok:
                return

            # Step 1: unload common conflicting modules
            unload_cmd = ['modprobe', '-r', 'dvb_usb_rtl28xxu', 'rtl2832_sdr', 'r820t', 'rtl2832']
            res = self._run_with_possible_privilege(unload_cmd, timeout=12)
            out_text = ''
            try:
                out_text += f"Unload result: returncode={res.returncode}\n"
                out_text += (getattr(res, 'stdout', '') or '') + '\n' + (getattr(res, 'stderr', '') or '')
            except Exception:
                out_text += 'Unload result: (no detailed output)\n'

            # Ask whether to install udev rule
            install_udev = messagebox.askyesno('Udev rule', 'Create a udev rule to grant device access to group "plugdev" (writes /etc/udev/rules.d/52-rtl-sdr.rules)?')
            if install_udev:
                udev_content = ('# RTL-SDR permissions for Realtek RTL2832U (vendor 0bda product 2838)\n'
                                'ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0664", GROUP="plugdev"\n')
                tmp_path = os.path.join(os.getcwd(), '.tmp_52-rtl-sdr.rules')
                try:
                    with open(tmp_path, 'w') as f:
                        f.write(udev_content)

                    # Backup existing file if present
                    target = '/etc/udev/rules.d/52-rtl-sdr.rules'
                    try:
                        stat_res = self._run_with_possible_privilege(['test', '-f', target], timeout=4)
                        # If test returned 0, file exists -> create backup
                        if getattr(stat_res, 'returncode', 1) == 0:
                            bak = target + '.bak'
                            self._run_with_possible_privilege(['cp', target, bak], timeout=6)
                            # record backup for potential revert
                            self._driver_fix_backups = getattr(self, '_driver_fix_backups', {})
                            self._driver_fix_backups['udev'] = bak
                    except Exception:
                        pass

                    mv_res = self._run_with_possible_privilege(['mv', tmp_path, target], timeout=8)
                    out_text += '\nudev move: ' + str(getattr(mv_res, 'returncode', '')) + '\n'
                except Exception as e:
                    out_text += f'Failed to write/move udev rule: {e}\n'

                # reload udev rules
                try:
                    reload_res = self._run_with_possible_privilege(['udevadm', 'control', '--reload'], timeout=6)
                    trigger_res = self._run_with_possible_privilege(['udevadm', 'trigger'], timeout=6)
                    out_text += f'udev reload return: {getattr(reload_res, "returncode", "")}, trigger: {getattr(trigger_res, "returncode", "")}\n'
                except Exception as e:
                    out_text += f'Failed to reload/trigger udev: {e}\n'

            install_blacklist = messagebox.askyesno('Blacklist module', 'Write a modprobe blacklist file to prevent DVB driver loading automatically (writes /etc/modprobe.d/blacklist-rtl.conf)?')
            if install_blacklist:
                bl_content = ('# Prevent DVB kernel driver from binding to RTL2832U dongles (for rtl-sdr usage)\n'
                              'blacklist dvb_usb_rtl28xxu\n'
                              'blacklist rtl2832_sdr\n'
                              'blacklist r820t\n')
                tmpb = os.path.join(os.getcwd(), '.tmp_blacklist-rtl.conf')
                try:
                    with open(tmpb, 'w') as f:
                        f.write(bl_content)
                    mvb = self._run_with_possible_privilege(['mv', tmpb, '/etc/modprobe.d/blacklist-rtl.conf'], timeout=8)
                    out_text += '\nblacklist move: ' + str(getattr(mvb, 'returncode', '')) + '\n'
                except Exception as e:
                    out_text += f'Failed to write/move blacklist file: {e}\n'

            # Summarize and show results
            def _show():
                try:
                    dlg = tk.Toplevel(self.root)
                    dlg.title('Driver Fix Results')
                    dlg.geometry('720x420')
                    txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
                    txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
                    txt.insert(tk.END, out_text)
                    txt.configure(state=tk.DISABLED)
                    tk.Button(dlg, text='Close', command=dlg.destroy).pack(pady=6)
                except Exception:
                    messagebox.showinfo('Driver Fix Results', out_text)

            try:
                self.root.after(0, _show)
            except Exception:
                messagebox.showinfo('Driver Fix Results', out_text)

        except Exception as e:
            logger.exception('Driver fix failed')
            messagebox.showerror('Driver Fix', f'Error while applying driver fixes: {e}')

    def run_rtl_test_as_root(self):
        """Run `rtl_test -t` with privilege if needed and show the output in a dialog."""
        out = ''
        try:
            res = self._run_with_possible_privilege(['rtl_test', '-t'], timeout=60)
            try:
                out = (getattr(res, 'stdout', '') or '') + '\n' + (getattr(res, 'stderr', '') or '')
            except Exception:
                out = str(res)
        except Exception as e:
            out = f'Error running rtl_test: {e}'

        def _show():
            try:
                dlg = tk.Toplevel(self.root)
                dlg.title('rtl_test output')
                dlg.geometry('720x420')
                txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
                txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
                txt.insert(tk.END, out)
                txt.configure(state=tk.DISABLED)
                tk.Button(dlg, text='Close', command=dlg.destroy).pack(pady=6)
            except Exception:
                messagebox.showinfo('rtl_test', out)

        try:
            self.root.after(0, _show)
        except Exception:
            pass

    def revert_driver_fix(self):
        """Undo driver fix by restoring backups or removing added files.

        Uses backups created during `run_driver_fix()` stored in `self._driver_fix_backups`.
        """
        try:
            ok = messagebox.askyesno('Undo Driver Fixes', 'Attempt to restore previous udev/blacklist files and reload udev? Continue?')
            if not ok:
                return

            out_text = ''
            backups = getattr(self, '_driver_fix_backups', {}) or {}

            # Restore udev rule
            target = '/etc/udev/rules.d/52-rtl-sdr.rules'
            if 'udev' in backups:
                bak = backups['udev']
                try:
                    mv_res = self._run_with_possible_privilege(['mv', bak, target], timeout=8)
                    out_text += f'Restored udev from {bak} -> {target}\n'
                except Exception as e:
                    out_text += f'Failed to restore udev backup: {e}\n'
            else:
                # No backup: remove the file we may have created
                try:
                    rm_res = self._run_with_possible_privilege(['rm', '-f', target], timeout=6)
                    out_text += f'Removed udev rule {target}\n'
                except Exception as e:
                    out_text += f'Failed to remove udev rule: {e}\n'

            # Restore blacklist
            target_b = '/etc/modprobe.d/blacklist-rtl.conf'
            if 'blacklist' in backups:
                bakb = backups['blacklist']
                try:
                    mvb = self._run_with_possible_privilege(['mv', bakb, target_b], timeout=8)
                    out_text += f'Restored blacklist from {bakb} -> {target_b}\n'
                except Exception as e:
                    out_text += f'Failed to restore blacklist backup: {e}\n'
            else:
                try:
                    self._run_with_possible_privilege(['rm', '-f', target_b], timeout=6)
                    out_text += f'Removed blacklist file {target_b}\n'
                except Exception as e:
                    out_text += f'Failed to remove blacklist file: {e}\n'

            # Reload udev
            try:
                reload_res = self._run_with_possible_privilege(['udevadm', 'control', '--reload'], timeout=6)
                trigger_res = self._run_with_possible_privilege(['udevadm', 'trigger'], timeout=6)
                out_text += f'udev reload return: {getattr(reload_res, "returncode", "")}, trigger: {getattr(trigger_res, "returncode", "")}\n'
            except Exception as e:
                out_text += f'Failed to reload/trigger udev: {e}\n'

            # Show results
            def _show():
                try:
                    dlg = tk.Toplevel(self.root)
                    dlg.title('Undo Driver Fix Results')
                    dlg.geometry('720x420')
                    txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
                    txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
                    txt.insert(tk.END, out_text)
                    txt.configure(state=tk.DISABLED)
                    tk.Button(dlg, text='Close', command=dlg.destroy).pack(pady=6)
                except Exception:
                    messagebox.showinfo('Undo Driver Fix Results', out_text)

            try:
                self.root.after(0, _show)
            except Exception:
                messagebox.showinfo('Undo Driver Fix Results', out_text)

        except Exception as e:
            logger.exception('Revert driver fix failed')
            messagebox.showerror('Undo Driver Fixes', f'Error while reverting driver fixes: {e}')

    def _on_root_config(self, event=None):
        """Debounced handler for root '<Configure>' events to update UI scaling."""
        try:
            if getattr(self, '_resize_after_id', None):
                try:
                    self.root.after_cancel(self._resize_after_id)
                except Exception:
                    pass
            self._resize_after_id = self.root.after(120, lambda: self._apply_ui_scale())
        except Exception:
            pass

    def _apply_ui_scale(self):
        """Adjust named font sizes based on current window width for responsive scaling."""
        try:
            w = max(400, self.root.winfo_width() or 800)
            # scale factor around 1000px baseline
            scale = max(0.7, min(1.6, w / 1000.0))
            # Apply sizes to named fonts
            try:
                if isinstance(self.title_font, tkfont.Font):
                    self.title_font.configure(size=max(10, int(18 * scale)))
                if isinstance(self.header_font, tkfont.Font):
                    self.header_font.configure(size=max(8, int(12 * scale)))
                if isinstance(self.stats_font, tkfont.Font):
                    self.stats_font.configure(size=max(9, int(14 * scale)))
                if isinstance(self.small_font, tkfont.Font):
                    self.small_font.configure(size=max(8, int(11 * scale)))
            except Exception:
                pass
            # Reflow action buttons to account for width changes
            try:
                self._reflow_action_buttons()
            except Exception:
                pass
        except Exception:
            pass

    def _reflow_action_buttons(self, event=None):
        """Reflow the action buttons into multiple rows based on available width.

        This swaps children from packed layout into a grid arrangement to allow
        wrapping when the window is narrow.
        """
        try:
            frame = getattr(self, 'action_frame', None)
            if frame is None:
                return
            for child in frame.winfo_children():
                try:
                    child.grid_forget()
                except Exception:
                    pass
                try:
                    child.pack_configure(fill='x')
                except Exception:
                    pass
        except Exception:
            pass

    def _get_bluetooth_state(self):
        """Return 'blocked' or 'unblocked' or None on error.

        Tries multiple methods (rfkill then bluetoothctl) to determine state.
        """
        try:
            # Prefer BlueZ DBus if available — more reliable than `rfkill`/`bluetoothctl` parsing
            if dbus is not None:
                try:
                    try:
                        system_bus = dbus.SystemBus()
                    except Exception:
                        system_bus = None
                    if system_bus is not None:
                        manager = dbus.Interface(
                            system_bus.get_object('org.bluez', '/'),
                            'org.freedesktop.DBus.ObjectManager'
                        )
                        objs = manager.GetManagedObjects()
                        for path, interfaces in objs.items():
                            if 'org.bluez.Adapter1' in interfaces:
                                props = interfaces['org.bluez.Adapter1']
                                # `Powered` property may be a boolean-like
                                powered = props.get('Powered')
                                if powered is True or str(powered).lower() == 'true':
                                    return 'unblocked'
                                if powered is False or str(powered).lower() == 'false':
                                    return 'blocked'
                except Exception:
                    logger.exception('DBus check for Bluetooth state failed')

            # Try rfkill first (common on many distros)
            try:
                try:
                    res = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True, timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning('rfkill list bluetooth timed out')
                    res = None
                if res:
                    out = (res.stdout or "") + (res.stderr or "")
                    out = out.lower()
                    if "soft blocked: yes" in out or "blocked: yes" in out:
                        return "blocked"
                    if "soft blocked: no" in out or "blocked: no" in out:
                        return "unblocked"
            except FileNotFoundError:
                # rfkill not available; fall through
                pass

            # Fall back to bluetoothctl show (looks for Powered: yes/no)
            try:
                try:
                    res = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning('bluetoothctl show timed out')
                    res = None
                if res:
                    out = (res.stdout or "") + (res.stderr or "")
                    out = out.lower()
                    if "powered: yes" in out:
                        return "unblocked"
                    if "powered: no" in out:
                        return "blocked"
            except FileNotFoundError:
                pass

            return None
        except Exception:
            return None

    def verify_connectivity(self, do_ble_scan: bool = False, ble_timeout: int = 5):
        """Verify Bluetooth connectivity using multiple methods.

        Returns a dict with keys: 'bluez', 'bluetoothctl', 'rfkill', 'ble_scan', 'ok'
        - 'bluez': 'unblocked'|'blocked'|None
        - 'bluetoothctl': same as above or raw output
        - 'rfkill': 'unblocked'|'blocked'|None
        - 'ble_scan': integer count of found advertising devices or None
        - 'ok': boolean true if any positive indication of Bluetooth availability
        """
        results = {'bluez': None, 'bluetoothctl': None, 'rfkill': None, 'ble_scan': None, 'ok': False}

        # Check BlueZ via DBus
        if dbus is not None:
            try:
                try:
                    system_bus = dbus.SystemBus()
                except Exception:
                    system_bus = None
                if system_bus is not None:
                    manager = dbus.Interface(
                        system_bus.get_object('org.bluez', '/'),
                        'org.freedesktop.DBus.ObjectManager'
                    )
                    objs = manager.GetManagedObjects()
                    for path, interfaces in objs.items():
                        if 'org.bluez.Adapter1' in interfaces:
                            props = interfaces['org.bluez.Adapter1']
                            powered = props.get('Powered')
                            if powered is True or str(powered).lower() == 'true':
                                results['bluez'] = 'unblocked'
                                results['ok'] = True
                                break
                            if powered is False or str(powered).lower() == 'false':
                                results['bluez'] = 'blocked'
            except Exception:
                logger.exception('verify_connectivity: BlueZ dbus check failed')

        # rfkill
        try:
            res = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True, timeout=4)
            out = (res.stdout or "") + (res.stderr or "")
            low = out.lower()
            if "soft blocked: yes" in low or "blocked: yes" in low:
                results['rfkill'] = 'blocked'
            elif "soft blocked: no" in low or "blocked: no" in low:
                results['rfkill'] = 'unblocked'
                results['ok'] = True
            else:
                results['rfkill'] = None
        except subprocess.TimeoutExpired:
            logger.warning('verify_connectivity: rfkill timed out')
        except FileNotFoundError:
            results['rfkill'] = None
        except Exception:
            logger.exception('verify_connectivity: rfkill check failed')

        # bluetoothctl
        try:
            res = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=4)
            out = (res.stdout or "") + (res.stderr or "")
            low = out.lower()
            if "powered: yes" in low:
                results['bluetoothctl'] = 'unblocked'
                results['ok'] = True
            elif "powered: no" in low:
                results['bluetoothctl'] = 'blocked'
            else:
                results['bluetoothctl'] = out.strip()
        except subprocess.TimeoutExpired:
            logger.warning('verify_connectivity: bluetoothctl timed out')
            results['bluetoothctl'] = 'timed out'
        except FileNotFoundError:
            results['bluetoothctl'] = None
        except Exception:
            logger.exception('verify_connectivity: bluetoothctl check failed')

        # Optional BLE scan using bleak to check radio is scanning/seeing adverts
        if do_ble_scan:
            try:
                try:
                    from bleak import BleakScanner
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    devices = loop.run_until_complete(BleakScanner.discover(timeout=ble_timeout))
                    results['ble_scan'] = len(devices) if devices is not None else 0
                    if results['ble_scan']:
                        results['ok'] = True
                except Exception:
                    logger.exception('verify_connectivity: BLE scan failed')
                    results['ble_scan'] = None
            finally:
                try:
                    asyncio.set_event_loop(None)
                except Exception:
                    pass

        return results

    def toggle_bluetooth(self):
        """Toggle bluetooth: try DBus/BlueZ first, then fall back to rfkill.

        Runs in a background thread so the UI doesn't block. Uses dbus-python
        if available to toggle the Adapter1.Powered property. If that fails
        (missing lib or permission), falls back to calling `rfkill`.
        """
        def worker():
            # Update UI immediately on main thread to give feedback
            try:
                self.root.after(0, lambda: self.status_bar.config(text="Toggling Bluetooth..."))
            except Exception:
                pass

            # Do not call tkinter APIs from this thread — collect results and apply them via root.after
            result = {'ok': False, 'method': None, 'action': None, 'message': None}

            print("toggle_bluetooth: worker started")
            logger.debug('toggle_bluetooth: worker started')
            # Try BlueZ via dbus-python first
            if dbus is not None:
                try:
                    system_bus = None
                    try:
                        system_bus = dbus.SystemBus()
                    except Exception:
                        system_bus = None

                    if system_bus is not None:
                        manager = dbus.Interface(
                            system_bus.get_object('org.bluez', '/'),
                            'org.freedesktop.DBus.ObjectManager'
                        )
                        objs = manager.GetManagedObjects()
                        adapter_path = None
                        for path, interfaces in objs.items():
                            if 'org.bluez.Adapter1' in interfaces:
                                adapter_path = path
                                break

                        if adapter_path:
                            props = dbus.Interface(
                                system_bus.get_object('org.bluez', adapter_path),
                                'org.freedesktop.DBus.Properties'
                            )
                            try:
                                powered = bool(props.Get('org.bluez.Adapter1', 'Powered'))
                            except Exception:
                                powered = None

                            # If we could read powered state, toggle it
                            if powered is not None:
                                try:
                                    props.Set('org.bluez.Adapter1', 'Powered', dbus.Boolean(not powered))
                                    new_state = 'on' if not powered else 'off'
                                    result.update({'ok': True, 'method': 'bluez-dbus', 'action': new_state,
                                                   'message': f'Bluetooth {new_state} (via BlueZ)'} )
                                    # apply LED and message in main thread
                                    self.root.after(0, lambda: [
                                        self.status_bar.config(text=result['message']),
                                        self._set_led(self.bt_led, 'on' if new_state == 'on' else 'off'),
                                        messagebox.showinfo('Bluetooth', result['message'])
                                    ])
                                    logger.info('Bluetooth toggled via BlueZ DBus: %s', new_state)
                                    return
                                except Exception as e:
                                    # proceed to other methods
                                    logger.exception('DBus set failed')
                except Exception as e:
                    logger.exception('DBus toggle failed')

            # Next try bluetoothctl (supports 'power on' / 'power off' and 'show')
            try:
                # Determine current powered state via bluetoothctl show
                powered = None
                try:
                    try:
                        res = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning('bluetoothctl show timed out (toggle)')
                        res = None
                    if res:
                        out = (res.stdout or "") + (res.stderr or "")
                        out_l = out.lower()
                        logger.debug('bluetoothctl show output: %s', out)
                        if "powered: yes" in out_l:
                            powered = True
                        elif "powered: no" in out_l:
                            powered = False
                except Exception:
                    powered = None

                # Decide desired action (toggle)
                if powered is None:
                    # If unknown, try to unblock via rfkill later — assume off and try to turn on
                    target = 'on'
                else:
                    target = 'off' if powered else 'on'

                logger.debug('Attempting bluetoothctl power %s', target)
                res = self._run_with_possible_privilege(["bluetoothctl", "power", target], timeout=8)
                logger.debug('bluetoothctl power result: %s', res)
                if res is not None and getattr(res, 'returncode', 1) == 0:
                    result.update({'ok': True, 'method': 'bluetoothctl', 'action': target,
                                   'message': f'Bluetooth {target} (via bluetoothctl)'} )
                    self.root.after(0, lambda: [
                        self.status_bar.config(text=result['message']),
                        self._set_led(self.bt_led, 'on' if target == 'on' else 'off'),
                        messagebox.showinfo('Bluetooth', result['message'])
                    ])
                    logger.info('Bluetooth toggled via bluetoothctl: %s', target)
                    return
                else:
                    if res is None:
                        logger.debug('bluetoothctl returned no result')
                    else:
                        logger.debug('bluetoothctl returned %s: %s %s', getattr(res,'returncode',None), getattr(res,'stdout',''), getattr(res,'stderr',''))
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"bluetoothctl toggle failed: {e}")

            # Fall back to rfkill
            try:
                state = self._get_bluetooth_state()
                if state == "blocked":
                    cmd = ["rfkill", "unblock", "bluetooth"]
                    action = "unblocked"
                elif state == "unblocked":
                    cmd = ["rfkill", "block", "bluetooth"]
                    action = "blocked"
                else:
                    cmd = ["rfkill", "unblock", "bluetooth"]
                    action = "unblocked"

                logger.debug('Attempting rfkill command: %s', cmd)
                res = self._run_with_possible_privilege(cmd, timeout=5)
                logger.debug('rfkill result: %s', res)
                if res is not None and getattr(res, 'returncode', 1) == 0:
                    result.update({'ok': True, 'method': 'rfkill', 'action': action,
                                   'message': f'Bluetooth {action} (via rfkill)'} )
                    self.root.after(0, lambda: [
                        self.status_bar.config(text=result['message']),
                        self._set_led(self.bt_led, 'on' if action == 'unblocked' else 'off'),
                        messagebox.showinfo('Bluetooth', result['message'])
                    ])
                    logger.info('Bluetooth toggled via rfkill: %s', action)
                    return
                else:
                    msg = (getattr(res, 'stderr', None) or getattr(res, 'stdout', None) or 'Unknown rfkill error')
                    result.update({'ok': False, 'method': 'rfkill', 'message': msg})
                    logger.debug('rfkill failed: %s', msg)
            except FileNotFoundError:
                result.update({'ok': False, 'method': 'none', 'message': 'rfkill not found'})
            except Exception as e:
                result.update({'ok': False, 'method': 'rfkill', 'message': str(e)})

            # If we reached here, no method succeeded — notify user on main thread
            def _notify_fail():
                self.status_bar.config(text="Bluetooth toggle failed")
                messagebox.showwarning(
                    "Bluetooth Toggle Failed",
                    "Could not toggle Bluetooth. Ask an admin to install a polkit rule or run:\n\nsudo rfkill unblock bluetooth\n\n" + (result.get('message') or '')
                )
                logger.warning('Bluetooth toggle failed: %s', result.get('message'))

            self.root.after(0, _notify_fail)

            # Start worker
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception as e:
            try:
                messagebox.showerror('Error', f'Could not start Bluetooth toggle thread: {e}')
            except Exception:
                print(f'Failed to start toggle thread: {e}')

    def seed_rng_from_sdr(self):
        """Collect entropy via atmospheric/quantum RNG (preferred) and seed the internal RNGCollector's DRBG.

        Runs in background and falls back to SDR or software RNG if needed.
        """
        def worker():
            self.status_bar.config(text="Seeding RNG from Quantum RNG (online preferred)...")
            try:
                # Detect SDR availability first
                from sdr_rng import is_sdr_available
                sdr_ok = is_sdr_available()
                # Request 64 bytes of entropy (aqrng prefers SDR first)
                seed = get_random_bytes(64)
            except Exception as e:
                seed = None
                sdr_ok = False
                print(f"SDR seed error: {e}")

            if seed:
                try:
                    self.rng_collector.seed_rng(seed)
                    self.status_bar.config(text="RNG seeded from SDR")
                    messagebox.showinfo("Seeded", "RNG successfully seeded from SDR entropy.")
                    # Update SDR status
                    self.sdr_status_label.config(text=f"SDR: {'available' if sdr_ok else 'used fallback'}")
                    # Update LEDs
                    self._set_led(self.rng_led, 'on')
                    self._set_led(self.sdr_led, 'on' if sdr_ok else 'off')
                except Exception as e:
                    self.status_bar.config(text="Seeding failed")
                    messagebox.showwarning("Seed failed", f"Could not seed RNG: {e}")
            else:
                # Fallback: get software RNG (aqrng already attempted SDR/online)
                sw = get_random_bytes(64)
                try:
                    self.rng_collector.seed_rng(sw)
                    self.status_bar.config(text="RNG seeded from software fallback")
                    messagebox.showwarning("Fallback", "SDR not available; seeded RNG from software fallback.")
                    self.sdr_status_label.config(text="SDR: not available (fallback used)")
                    self._set_led(self.rng_led, 'on')
                    self._set_led(self.sdr_led, 'off')
                except Exception as e:
                    self.status_bar.config(text="Seeding error")
                    messagebox.showerror("Seed error", f"Seeding failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _hrv_consumer(self):
        """Background consumer that reads HRV samples placed on `self.coherence_queue`
        by `HRVDeviceManager` and records them into `rng_collector` for correlation
        analysis (bit-index aligned snapshots).
        """
        try:
            while True:
                try:
                    sample = self.coherence_queue.get(timeout=1.0)
                except Empty:
                    continue
                try:
                    # sample is expected to be a dict from HRVDeviceManager
                    self.rng_collector.record_hrv_snapshot(sample)
                    # Update UI stream (must run on main thread)
                    try:
                        if getattr(self, 'hrv_stream_box', None) is not None:
                            self.root.after(0, lambda s=sample: self._append_hrv_stream(s))
                    except Exception:
                        pass
                except Exception:
                    logger.exception('Failed to record HRV snapshot')
        except Exception:
            logger.exception('HRV consumer exiting')

    def _toggle_hrv_graph_test(self):
        """Start/stop a synthetic HRV generator that pushes samples to the coherence queue."""
        try:
            running = getattr(self, '_hrv_test_running', False)
            if running:
                # stop
                self._hrv_test_running = False
                try:
                    if getattr(self, 'hrv_graph_test_btn', None) is not None:
                        self.hrv_graph_test_btn.config(text='Graph Test')
                except Exception:
                    pass
                try:
                    self.status_bar.config(text='HRV graph test stopped')
                except Exception:
                    pass
                return

            # start
            self._hrv_test_running = True
            self._hrv_test_thread = threading.Thread(target=self._hrv_graph_test_worker, daemon=True)
            self._hrv_test_thread.start()
            try:
                if getattr(self, 'hrv_graph_test_btn', None) is not None:
                    self.hrv_graph_test_btn.config(text='Stop Graph Test')
            except Exception:
                pass
            try:
                self.status_bar.config(text='HRV graph test running')
            except Exception:
                pass
        except Exception:
            pass

    def _hrv_graph_test_worker(self):
        """Worker that emits synthetic coherence values periodically to the coherence queue."""
        try:
            import random, math
            start = time.time()
            while getattr(self, '_hrv_test_running', False):
                t = time.time() - start
                # slow sinusoidal coherence between 0.1 and 0.9 with small noise
                coh = 0.5 + 0.4 * math.sin(2 * math.pi * (t / 6.0)) + random.uniform(-0.05, 0.05)
                coh = max(0.0, min(1.0, coh))
                sample = {
                    'timestamp': time.time(),
                    'device': 'graph-test',
                    'heart_rate': 60 + int(5 * math.sin(t)),
                    'coherence': coh,
                    'rr_intervals': []
                }
                try:
                    self.coherence_queue.put(sample, block=False)
                except Exception:
                    try:
                        self.coherence_queue.put(sample)
                    except Exception:
                        pass
                time.sleep(0.5)
        except Exception:
            logger.exception('HRV graph test worker failed')
        finally:
            try:
                self._hrv_test_running = False
                if getattr(self, 'hrv_graph_test_btn', None) is not None:
                    try:
                        self.hrv_graph_test_btn.config(text='Graph Test')
                    except Exception:
                        pass
                try:
                    self.status_bar.config(text='HRV graph test stopped')
                except Exception:
                    pass
            except Exception:
                pass

    def _append_hrv_stream(self, sample: dict):
        """Append a formatted HRV sample to the on-screen stream box.

        This runs on the main/UI thread.
        """
        try:
            box = getattr(self, 'hrv_stream_box', None)
            if box is None:
                return
            # Format a compact single-line summary
            ts = datetime.fromtimestamp(sample.get('timestamp', time.time())).isoformat()
            dev = sample.get('device', 'unknown')
            hr = sample.get('heart_rate', 'n/a')
            coh = sample.get('coherence', 0.0)
            bi = sample.get('bit_index', None) or len(self.rng_collector.bits)
            rr = sample.get('rr_intervals', [])
            txt = f"{ts} | {dev} | HR={hr} | coh={coh:.3f} | bit_index={bi} | rr_count={len(rr)}\n"

            # Insert and keep read-only
            box.configure(state=tk.NORMAL)
            box.insert(tk.END, txt)
            # Trim to reasonable size (keep ~200 lines)
            lines = int(box.index('end-1c').split('.')[0])
            if lines > 250:
                # delete oldest lines
                box.delete('1.0', f'{lines-200}.0')
            box.see(tk.END)
            box.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Update sparkline history and redraw
        try:
            coh = float(sample.get('coherence', 0.0) or 0.0)
            if self._hrv_sparkline_enabled:
                self._hrv_coherence_history.append(coh)
                try:
                    if getattr(self, 'hrv_spark_canvas', None) is not None:
                        self._draw_hrv_sparkline()
                except Exception:
                    pass
            try:
                if getattr(self, 'hrv_spark_label', None) is not None:
                    self.hrv_spark_label.config(text=f"Coh: {coh:.3f}")
            except Exception:
                pass
        except Exception:
            pass
        # If matplotlib figure is present, schedule an update of the embedded plot
        try:
            if getattr(self, '_hrv_fig', None) is not None and getattr(self, '_hrv_line', None) is not None:
                try:
                    # schedule on main thread
                    self.root.after(0, self._update_hrv_plot)
                except Exception:
                    pass
        except Exception:
            pass

    def _draw_hrv_sparkline(self):
        """Draw the coherence sparkline onto the canvas. Assumes called on main thread."""
        try:
            canvas = getattr(self, 'hrv_spark_canvas', None)
            if canvas is None:
                return
            data = list(self._hrv_coherence_history)
            w = max(100, canvas.winfo_width() or 300)
            h = max(20, canvas.winfo_height() or 60)
            canvas.delete('all')
            if not data:
                # draw baseline
                canvas.create_line(0, h/2, w, h/2, fill='#ddd')
                return

            # scale data 0..1 to canvas height (invert y)
            mx = max(1.0, max(data))
            mn = min(0.0, min(data))
            span = mx - mn if (mx - mn) > 0 else 1.0
            # pad left/right
            left_pad = 4
            right_pad = 4
            usable_w = w - left_pad - right_pad
            step = usable_w / max(1, (len(data)-1))
            points = []
            for i, v in enumerate(data):
                x = left_pad + i * step
                # normalize
                nv = (v - mn) / span
                y = h - (nv * (h - 6)) - 3
                points.append((x, y))

            # draw polyline
            for i in range(len(points)-1):
                x1,y1 = points[i]
                x2,y2 = points[i+1]
                canvas.create_line(x1,y1,x2,y2, fill='#2c3e50', width=2)

            # draw latest point
            lx,ly = points[-1]
            canvas.create_oval(lx-3, ly-3, lx+3, ly+3, fill='#e67e22', outline='')
        except Exception:
            pass

    # --- Matplotlib / HRV plot control helpers ---
    def _toggle_hrv_plot_pause(self):
        """Toggle pause/resume for the embedded HRV Matplotlib plot."""
        try:
            self._hrv_plot_paused = not getattr(self, '_hrv_plot_paused', False)
            if getattr(self, 'hrv_pause_btn', None) is not None:
                try:
                    self.hrv_pause_btn.config(text='Resume' if self._hrv_plot_paused else 'Pause')
                except Exception:
                    pass
        except Exception:
            pass

    def _set_hrv_history(self, n: int):
        """Adjust the history length for HRV coherence plotting."""
        try:
            n = max(10, int(n))
            cur = getattr(self, '_hrv_coherence_history', None)
            if cur is None:
                self._hrv_coherence_history = deque(maxlen=n)
            else:
                # preserve existing data
                data = list(cur)
                self._hrv_coherence_history = deque(data[-n:], maxlen=n)
            self._hrv_history_len = n
            # update axes if using matplotlib
            if getattr(self, '_hrv_ax', None) is not None:
                try:
                    self._hrv_ax.set_xlim(0, n)
                    if getattr(self, '_hrv_canvas', None) is not None:
                        try:
                            self._hrv_canvas.draw_idle()
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

    def _export_hrv_csv(self):
        """Export recorded HRV snapshots to CSV."""
        try:
            if not getattr(self.rng_collector, 'hrv_snapshots', None):
                messagebox.showinfo('Export HRV', 'No HRV snapshots to export')
                return
            path = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV','*.csv')], initialfile='hrv_snapshots.csv')
            if not path:
                return
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp', 'device', 'heart_rate', 'coherence', 'bit_index', 'rr_intervals'])
                snapshots = list(self.rng_collector.hrv_snapshots)
                if getattr(self, 'admin_mode', 'external') != 'external':
                    # redacted in self-admin mode
                    messagebox.showinfo('Export HRV', 'HRV snapshots are redacted in self-admin mode')
                    return
                for s in snapshots:
                    writer.writerow([s.get('timestamp'), s.get('device'), s.get('heart_rate'), s.get('coherence'), s.get('bit_index'), json.dumps(s.get('rr_intervals'))])
            messagebox.showinfo('Export HRV', f'HRV snapshots exported to {path}')
        except Exception as e:
            logger.exception('Export HRV CSV failed')
            messagebox.showerror('Export HRV', f'Export failed: {e}')

    def _export_hrv_png(self):
        """Export the current HRV plot to PNG (if matplotlib available)."""
        try:
            if not getattr(self, '_hrv_fig', None):
                messagebox.showinfo('Export PNG', 'Matplotlib not available or no figure to export')
                return
            path = filedialog.asksaveasfilename(defaultextension='.png', filetypes=[('PNG','*.png')], initialfile='hrv_plot.png')
            if not path:
                return
            try:
                self._hrv_fig.savefig(path, dpi=150)
                messagebox.showinfo('Export PNG', f'HRV plot saved to {path}')
            except Exception:
                # fallback: try canvas print to postscript then convert (best-effort)
                try:
                    self._hrv_canvas.print_figure(path)
                    messagebox.showinfo('Export PNG', f'HRV plot saved to {path}')
                except Exception as e:
                    logger.exception('Export HRV PNG failed')
                    messagebox.showerror('Export PNG', f'Could not save PNG: {e}')
        except Exception:
            pass

    def _update_hrv_plot(self):
        """Update the embedded Matplotlib HRV line with data from the history deque."""
        try:
            if getattr(self, '_hrv_plot_paused', False):
                return
            if getattr(self, '_hrv_ax', None) is None or getattr(self, '_hrv_line', None) is None:
                return
            data = list(getattr(self, '_hrv_coherence_history', []))
            if not data:
                # clear line
                try:
                    self._hrv_line.set_data([], [])
                    self._hrv_ax.set_xlim(0, self._hrv_history_len)
                    self._hrv_ax.set_ylim(0, 1)
                    if getattr(self, '_hrv_canvas', None) is not None:
                        try:
                            self._hrv_canvas.draw_idle()
                        except Exception:
                            pass
                except Exception:
                    pass
                return

            x = list(range(max(0, len(data) - self._hrv_history_len), max(0, len(data) - self._hrv_history_len) + len(data)))
            # ensure arrays same length
            try:
                self._hrv_line.set_data(range(len(data)), data)
                self._hrv_ax.set_xlim(0, max(self._hrv_history_len, len(data)))
                # auto-scale y in [0,1]
                self._hrv_ax.set_ylim(0, 1)
                if getattr(self, '_hrv_canvas', None) is not None:
                    try:
                        # prefer draw_idle if available, fallback to draw
                        if hasattr(self._hrv_canvas, 'draw_idle'):
                            self._hrv_canvas.draw_idle()
                        else:
                            try:
                                self._hrv_canvas.draw()
                            except Exception:
                                # final fallback: call figure canvas draw via canvas manager
                                try:
                                    self._hrv_fig.canvas.draw()
                                except Exception:
                                    pass
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            pass

    def _sdr_provider_factory(self, chunk_bytes=1024, sdr_params=None):
        """Return a callable that yields raw bytes for the SDR stream.

        The callable will attempt to use `sdr_rng.SDRRNG` and fall back to
        `aqrng.get_random_bytes` or `secrets.token_bytes` if SDR unavailable.
        """
        sdr_params = sdr_params or {}

        def provider():
            try:
                from sdr_rng import SDRRNG
                # instantiate per-call is expensive; instantiate once
            except Exception:
                SDRRNG = None

            # Use a persistent instance stored on self to avoid repeated init
            if getattr(self, '_sdr_instance', None) is None:
                if SDRRNG is None:
                    raise RuntimeError('SDR libraries not available (install pyrtlsdr + numpy)')

                # Try a sequence of initialization parameter sets to help older
                # R820T dongles that sometimes fail to lock at higher sample
                # rates or with aggressive buffer sizes. Record attempts in
                # the log and try progressively more conservative settings.
                tried = []
                param_sets = []
                # Merge any user-provided sdr_params into trial sets
                base = dict(sdr_params or {})
                # Try preferred/default first
                param_sets.append({**base})
                # Lower sample rate and buffer sizes
                param_sets.append({**base, 'sample_rate': 2.048e6, 'samples_per_hash': 16384})
                # Try explicit maximum gain
                param_sets.append({**base, 'sample_rate': 2.048e6, 'samples_per_hash': 8192, 'gain': 49.6})
                # Try minimal gain
                param_sets.append({**base, 'sample_rate': 1.024e6, 'samples_per_hash': 4096, 'gain': 0})

                last_exc = None
                for p in param_sets:
                    try:
                        logger.debug('Attempting SDR init with params: %s', p)
                        self._sdr_instance = SDRRNG(**p)
                        # success
                        logger.info('SDR initialized with params: %s', p)
                        break
                    except Exception as exc:
                        logger.warning('SDR init failed with params %s: %s', p, exc)
                        tried.append((p, str(exc)))
                        last_exc = exc
                        self._sdr_instance = None

                if self._sdr_instance is None:
                    # Provide a helpful error including attempts
                    msg = 'SDR initialization failed after尝试: ' + '; '.join([f"{t[0]} -> {t[1]}" for t in tried])
                    logger.error(msg)
                    raise RuntimeError(msg) from last_exc

            try:
                raw = self._sdr_instance._collect_raw_bytes()
                # Update last-known center frequency for UI (Hz)
                try:
                    cf = getattr(self._sdr_instance, 'center_freq', None)
                    if cf is not None:
                        self._sdr_last_freq = float(cf)
                except Exception:
                    pass
                # Attempt to compute measured peak frequency if supported
                try:
                    if getattr(self, '_sdr_spectral_enabled', True) and hasattr(self._sdr_instance, 'get_peak_frequency'):
                        pf = self._sdr_instance.get_peak_frequency()
                        if pf is not None:
                            self._sdr_last_measured_freq = float(pf)
                except Exception:
                    pass
                if not raw:
                    raise RuntimeError('SDR returned no data')
                return raw
            except Exception as exc:
                # Close the SDR so next attempt will re-open cleanly
                try:
                    if getattr(self, '_sdr_instance', None) is not None:
                        close_fn = getattr(self._sdr_instance, 'close', None)
                        if callable(close_fn):
                            close_fn()
                finally:
                    self._sdr_instance = None
                raise RuntimeError(f'SDR read failed: {exc}') from exc

        return provider

    def toggle_sdr_stream(self):
        """Start/stop continuous SDR streaming into the RNGCollector."""
        # Respect temporary disable/backoff window after repeated failures
        now = time.time()
        if getattr(self, '_sdr_disabled_until', 0) and now < getattr(self, '_sdr_disabled_until', 0):
            rem = int(self._sdr_disabled_until - now)
            messagebox.showwarning('SDR Disabled', f'SDR controls temporarily disabled due to repeated errors. Retry in {rem} seconds.')
            return

        if not getattr(self, '_sdr_streaming', False):
            # Attempt to start; if provider init fails repeatedly, back off and disable SDR UI
            try:
                provider = self._sdr_provider_factory(1024)
                self.rng_collector.start_sdr_stream(provider)
                self._sdr_streaming = True
                self.sdr_stream_btn.config(text="  Stop SDR Stream")
                self.status_bar.config(text="SDR stream started")
                self._set_led(self.sdr_led, 'on')
                # initialize bits counter snapshot for throughput calc
                try:
                    self._sdr_last_bits_count = len(self.rng_collector.bits)
                    self._sdr_throughput_last_time = time.time()
                except Exception:
                    self._sdr_last_bits_count = 0
                    self._sdr_throughput_last_time = None
                # reset failure count on success
                self._sdr_fail_count = 0
            except Exception as e:
                logger.exception('Failed to start SDR stream')
                # increment failure counter and possibly disable SDR controls
                try:
                    self._sdr_fail_count = getattr(self, '_sdr_fail_count', 0) + 1
                except Exception:
                    self._sdr_fail_count = 1

                if self._sdr_fail_count >= getattr(self, '_sdr_fail_threshold', 3):
                    # Apply backoff and disable UI controls
                    self._sdr_disabled_until = time.time() + getattr(self, '_sdr_fail_backoff_secs', 300)
                    try:
                        self.sdr_stream_btn.config(text="SDR Disabled (errors)")
                        try:
                            self.sdr_stream_btn.state(['disabled'])
                        except Exception:
                            self.sdr_stream_btn.config(state='disabled')
                    except Exception:
                        pass
                    msg = (f"SDR failed to start {self._sdr_fail_count} times.\n"
                           "Controls have been disabled temporarily.\n"
                           "Would you like to run a quick diagnostics (rtl_test -t)?")
                    if messagebox.askyesno('SDR Error', msg):
                        # Run diagnostics in background thread
                        threading.Thread(target=self._run_sdr_diagnostics, daemon=True).start()
                else:
                    messagebox.showwarning('SDR Stream', f'Could not start SDR streaming: {e}\n(Attempt {self._sdr_fail_count}/{getattr(self, "_sdr_fail_threshold")})')
        else:
            try:
                self.rng_collector.stop_sdr_stream()
            except Exception:
                pass
            try:
                if getattr(self, '_sdr_instance', None) is not None:
                    close_fn = getattr(self._sdr_instance, 'close', None)
                    if callable(close_fn):
                        close_fn()
            except Exception:
                pass
            finally:
                self._sdr_instance = None
            self._sdr_streaming = False
            self.sdr_stream_btn.config(text="  Start SDR Stream")
            self.status_bar.config(text="SDR stream stopped")
            self._set_led(self.sdr_led, 'off')

    def _run_sdr_diagnostics(self):
        """Run `rtl_test -t` (best-effort) and show output in a dialog.

        Runs in a background thread; uses `self.root.after` to display results.
        """
        out = ''
        try:
            # Try to run rtl_test; may not be present on all systems
            proc = subprocess.run(['rtl_test', '-t'], capture_output=True, text=True, timeout=30)
            out = (proc.stdout or '') + '\n' + (proc.stderr or '')
        except FileNotFoundError:
            out = 'rtl_test not found on this system. Install rtl-sdr package to run diagnostics.'
        except subprocess.TimeoutExpired:
            out = 'rtl_test timed out.'
        except Exception as e:
            out = f'Error running rtl_test: {e}'

        def _show():
            try:
                dlg = tk.Toplevel(self.root)
                dlg.title('SDR Diagnostics')
                dlg.geometry('720x420')
                txt = scrolledtext.ScrolledText(dlg, wrap=tk.WORD)
                txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
                txt.insert(tk.END, out)
                txt.configure(state=tk.DISABLED)
                tk.Button(dlg, text='Close', command=dlg.destroy).pack(pady=6)
            except Exception:
                messagebox.showinfo('SDR Diagnostics', out)

        try:
            self.root.after(0, _show)
        except Exception:
            pass

    def _on_spectral_toggle(self):
        """Callback when spectral analysis checkbox is toggled."""
        try:
            self._sdr_spectral_enabled = bool(self._sdr_spectral_enabled_var.get())
            self.status_bar.config(text=f"Spectral analysis {'enabled' if self._sdr_spectral_enabled else 'disabled'}")
        except Exception:
            pass

    def import_baseline(self):
        """Import baseline data from a file (JSON or CSV)."""
        path = filedialog.askopenfilename(title='Select baseline file', filetypes=[('JSON','*.json'),('CSV','*.csv'),('All','*.*')])
        if not path:
            return
        try:
            bits = None
            if path.endswith('.json'):
                with open(path, 'r') as f:
                    obj = json.load(f)
                # Accept several formats: raw_bits list, baseline_bits list, hex string
                if isinstance(obj, dict):
                    if 'baseline_bits' in obj and isinstance(obj['baseline_bits'], list):
                        bits = obj['baseline_bits']
                    elif 'raw_bits' in obj and isinstance(obj['raw_bits'], list):
                        bits = obj['raw_bits']
                    elif 'seed' in obj and isinstance(obj['seed'], str):
                        # hex seed -> unpack
                        try:
                            b = bytes.fromhex(obj['seed'])
                            bits = []
                            for byte in b:
                                for i in range(8):
                                    bits.append((byte >> i) & 1)
                        except Exception:
                            bits = None
                elif isinstance(obj, list) and all(isinstance(x, int) for x in obj):
                    bits = obj
            else:
                # Try CSV, assume a column of 0/1 or hex
                bits = []
                with open(path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        # Accept lines with digits separated by commas
                        parts = [p.strip() for p in line.split(',') if p.strip()]
                        for p in parts:
                            if p in ('0','1'):
                                bits.append(int(p))
                            else:
                                # try parse hex
                                try:
                                    b = bytes.fromhex(p)
                                    for byte in b:
                                        for i in range(8):
                                            bits.append((byte >> i) & 1)
                                except Exception:
                                    continue

            if bits is None:
                messagebox.showwarning('Import Baseline', 'Could not parse baseline file')
                return

            ok = self.rng_collector.import_baseline_bits(bits)
            if ok:
                messagebox.showinfo('Import Baseline', f'Imported {len(self.rng_collector.baseline_bits)} baseline bits')
                self._audit_event('import-baseline', {'path': path, 'count': len(self.rng_collector.baseline_bits)})
            else:
                messagebox.showwarning('Import Baseline', 'Failed to import baseline bits')
        except Exception as e:
            logger.exception('Import baseline failed')
            messagebox.showerror('Import Baseline', f'Error importing baseline: {e}')

    def compare_baseline(self):
        """Compare currently imported baseline to current session using existing API."""
        try:
            comp = self.rng_collector.get_baseline_comparison()
            if comp is None:
                messagebox.showinfo('Compare Baseline', 'Not enough data to compare (need at least 100 bits in each)')
                return
            txt = (f"Baseline mean: {comp['baseline_mean']:.6f}\n"
                   f"Experiment mean: {comp['experiment_mean']:.6f}\n"
                   f"Effect percent: {comp['effect_percent']:+.4f}%\n"
                   f"Baseline bits: {comp['baseline_bits']}, Experiment bits: {comp['experiment_bits']}")
            messagebox.showinfo('Baseline Comparison', txt)
            self._audit_event('compare-baseline', comp)
        except Exception as e:
            logger.exception('Baseline compare failed')
            messagebox.showerror('Compare Baseline', f'Error comparing baseline: {e}')

        
    def on_closing(self):
        if self.running:
            if messagebox.askokcancel("Quit", "Stop current session and exit?"):
                self.stop_session()
                self.root.destroy()
        else:
            self.root.destroy()

if __name__ == "__main__":
    app = ConsciousnessLab()
    app.root.mainloop()
