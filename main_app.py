import tkinter as tk
from tkinter import messagebox, filedialog, ttk, simpledialog
import tkinter.scrolledtext as scrolledtext
import threading
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
from sdr_rng import get_random_bytes
from group_session import GroupSessionManager
from queue import Queue
import getpass
import pathlib

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
        self.root.geometry("650x650")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Core components
        self.coherence_queue = Queue()
#        self.hrv_manager = HRVDeviceManager()
#        self.hrv_manager = HRVDeviceManager()
        self.hrv_manager = HRVDeviceManager(self.coherence_queue)  
        self.rng_collector = RNGCollector()
        self.group_manager = None
        
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
        self.title_font = ("Helvetica", 18, "bold")
        self.header_font = ("Helvetica", 12, "bold")
        self.root.configure(bg=self.bg_color)

        # ttk style
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('Accent.TButton', background=self.accent_color, foreground='white', padding=6)

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

        self._icons['scan'] = _make_icon('#3498db')
        self._icons['baseline'] = _make_icon('#9b59b6')
        self._icons['experiment'] = _make_icon('#27ae60')
        self._icons['seed'] = _make_icon('#8e44ad')
        self._icons['bt'] = _make_icon('#16a085')
        self._icons['export'] = _make_icon('#34495e')
        self._icons['group'] = _make_icon('#f39c12')

        # Title
        tk.Label(self.root, text="Consciousness Field Lab",
                 font=self.title_font, bg=self.bg_color, fg=self.accent_color).pack(pady=10)
        
        # HRV Section
        hrv_frame = tk.LabelFrame(self.root, text="HRV Devices", padx=15, pady=10, bg=self.panel_color)
        hrv_frame.pack(fill="x", padx=20, pady=5)
        
        scan_btn = ttk.Button(hrv_frame, text="  Scan Devices", command=self.scan_devices, style='Accent.TButton', image=self._icons['scan'], compound=tk.LEFT)
        scan_btn.config(width=15)
        self.scan_btn = scan_btn
        scan_btn.pack(pady=5)

        # Tooltip helper (attached later)
        
        self.device_list = tk.Frame(hrv_frame)
        self.device_list.pack(fill="both", expand=True)
        
        # Stats Display
        stats_frame = tk.LabelFrame(self.root, text="Live Statistics", padx=15, pady=10, bg=self.panel_color)
        stats_frame.pack(fill="x", padx=20, pady=5)
        
        self.mode_label = tk.Label(stats_frame, text="Mode: IDLE",
                     font=self.header_font, fg="#7f8c8d", bg=self.panel_color)
        self.mode_label.pack()
        
        self.stats_label = tk.Label(stats_frame, text="Waiting to start...",
                      font=("Helvetica", 14), bg=self.panel_color)
        self.stats_label.pack(pady=5)
        
        self.effect_label = tk.Label(stats_frame, text="", font=("Arial", 11))
        self.effect_label.pack()
        
        self.coherence_label = tk.Label(stats_frame, text="", font=("Arial", 11))
        self.coherence_label.pack()
        
        # Participant Display (for group sessions)
        self.participant_frame = tk.LabelFrame(self.root, text="Active Participants", padx=15, pady=10, bg=self.panel_color)
        # Show participant frame (placeholder until group session starts)
        self.participant_frame.pack(fill="x", padx=20, pady=5, before=self.status_bar)
        tk.Label(self.participant_frame, text="No participants", fg="#95a5a6", bg=self.panel_color).pack()
        
        self.participant_labels = {}
        
        # Control Panel (reorganized)
        control_frame = tk.Frame(self.root, pady=10, bg=self.bg_color)
        control_frame.pack(fill='x', padx=20)

        left_controls = tk.Frame(control_frame, bg=self.bg_color)
        left_controls.pack(side='left', anchor='w')

        self.baseline_btn = ttk.Button(left_controls, text="  Run Baseline",
                 command=lambda: self.toggle_session("baseline"),
                 style='Accent.TButton', image=self._icons['baseline'], compound=tk.LEFT)
        self.baseline_btn.config(width=16)
        self.baseline_btn.grid(row=0, column=0, padx=6)

        self.experiment_btn = ttk.Button(left_controls, text="  Start Experiment",
                   command=lambda: self.toggle_session("experiment"),
                   style='Accent.TButton', image=self._icons['experiment'], compound=tk.LEFT)
        self.experiment_btn.config(width=16)
        self.experiment_btn.grid(row=0, column=1, padx=6)

        # Session duration presets (minutes)
        right_controls = tk.Frame(control_frame, bg=self.bg_color)
        right_controls.pack(side='right', anchor='e')

        tk.Label(right_controls, text="Duration:").grid(row=0, column=0, padx=(0,6))
        self.duration_var = tk.StringVar(value="5")
        presets = ["0.5","1","5","10","30","60"]
        self.duration_menu = tk.OptionMenu(right_controls, self.duration_var, *presets)
        self.duration_menu.config(width=6)
        self.duration_menu.grid(row=0, column=1)

        self.countdown_label = tk.Label(right_controls, text="Time left: --:--", width=14)
        self.countdown_label.grid(row=0, column=2, padx=(10,0))
        # Subject info shown only to external admins
        self.subject_info_label = tk.Label(right_controls, text="Subject: (none)", width=28, anchor='w', bg=self.bg_color)
        self.subject_info_label.grid(row=1, column=0, columnspan=3, pady=(6,0))
        
        # Quick Actions
        action_frame = tk.Frame(self.root, bg=self.bg_color)
        action_frame.pack(pady=8)
        
        self.mark_btn = ttk.Button(action_frame, text="  Mark Intention", command=self.mark_intention, image=self._icons['group'], compound=tk.LEFT)
        self.mark_btn.pack(side="left", padx=6)

        self.group_btn = ttk.Button(action_frame, text="  Group Session", command=self.start_group_session, image=self._icons['group'], compound=tk.LEFT)
        self.group_btn.pack(side="left", padx=6)

        self.export_btn = ttk.Button(action_frame, text="  Export Session", command=self.export_session, image=self._icons['export'], compound=tk.LEFT)
        self.export_btn.pack(side="left", padx=6)

        self.toggle_bt_btn = ttk.Button(action_frame, text="  Toggle Bluetooth", command=self.toggle_bluetooth, image=self._icons['bt'], compound=tk.LEFT)
        self.toggle_bt_btn.pack(side="left", padx=6)

        self.seed_btn = ttk.Button(action_frame, text="  Seed RNG (SDR)", command=self.seed_rng_from_sdr, image=self._icons['seed'], compound=tk.LEFT)
        self.seed_btn.pack(side="left", padx=6)

        # HRV stream test button
        self.hrv_test_btn = ttk.Button(action_frame, text="  HRV Diagnostics", command=self.test_hrv_stream)
        self.hrv_test_btn.pack(side="left", padx=6)

        # Bluetooth debug button
        self.bt_debug_btn = ttk.Button(action_frame, text="  BT Debug", command=self.bt_debug)
        self.bt_debug_btn.pack(side="left", padx=6)
        # Admin mode quick buttons (visible to the operator)
        admin_frame = tk.Frame(action_frame, bg=self.bg_color)
        admin_frame.pack(side="left", padx=12)

        self.self_admin_btn = ttk.Button(admin_frame, text="Self-Admin", command=lambda: self.set_admin_mode('self'))
        self.self_admin_btn.pack(side='left', padx=4)

        self.external_admin_btn = ttk.Button(admin_frame, text="External Admin", command=lambda: self.set_admin_mode('external'))
        self.external_admin_btn.pack(side='left', padx=4)

        # One-click start test: switch to self-admin and start experiment
        self.start_test_btn = ttk.Button(admin_frame, text="Start Test (Self-Admin)", command=self.start_test_self_admin)
        self.start_test_btn.pack(side='left', padx=8)

        # End test button
        self.end_test_btn = ttk.Button(admin_frame, text="End Test", command=self.end_test)
        self.end_test_btn.pack(side='left', padx=4)

        # apply accent style to important buttons
        for b in (self.baseline_btn, self.experiment_btn, scan_btn):
            try:
                b.configure(style='Accent.TButton')
            except Exception:
                pass

        # Small status indicators
        status_frame = tk.Frame(self.root, bg=self.bg_color)
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

        # SDR status
        self.sdr_status_label = tk.Label(self.root, text="SDR: unknown", bd=1,
                         relief=tk.GROOVE, anchor=tk.W, bg=self.bg_color)
        self.sdr_status_label.pack(side=tk.BOTTOM, fill=tk.X)

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
            
    def mark_intention(self):
        if not self.running or self.rng_collector.mode == "baseline":
            messagebox.showinfo("Info", "Run an experiment to mark intentions")
            return
            
        coherence_data = self.hrv_manager.get_all_coherence()
        self.rng_collector.mark_event("intention", coherence_data)
        self.status_bar.config(text=f"Marked intention at bit {self.rng_collector.get_stats()['count']}")
        
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
                    
        # Schedule next update
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
                'raw_bits': (list(self.rng_collector.bits)[-10000:] if getattr(self, 'admin_mode', 'external') == 'external' else None)
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
            (self.hrv_test_btn, "Check selected HRV devices are sending heart-rate / RR data."),
            (self.bt_debug_btn, "Run quick bluetoothctl/rfkill diagnostics."),
            (self.seed_btn, "Seed RNG from attached RTL-SDR device, or fall back to software RNG."),
            (self.export_btn, "Export current session data to CSV or JSON."),
            (self.scan_btn, "Scan for HRV BLE devices nearby."),
        ]

        for w, t in tips:
            try:
                _ToolTip(w, t)
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
        """Collect entropy via SDR and seed the internal RNGCollector's DRBG.

        Runs in background and falls back to software RNG if SDR isn't available.
        """
        def worker():
            self.status_bar.config(text="Seeding RNG from SDR...")
            try:
                # Detect SDR availability first
                from sdr_rng import is_sdr_available
                sdr_ok = is_sdr_available()
                # Request 64 bytes of entropy (whitened inside sdr_rng)
                seed = get_random_bytes(64, prefer_sdr=True)
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
                # Fallback: inform user that we used software RNG
                sw = get_random_bytes(64, prefer_sdr=False)
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
