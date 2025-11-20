import tkinter as tk
from tkinter import messagebox, filedialog, ttk, simpledialog
import tkinter.scrolledtext as scrolledtext
import threading
import os
import asyncio
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
        # Don't pack initially
        
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
            self.toggle_session("experiment")
            
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
                if selected:
                    self.hrv_manager.connect_devices(selected)
                    self.status_bar.config(text=f"Connected to {len(selected)} device(s)")
            
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

    def enter_external_admin(self):
        """Attempt to enter External Admin mode. Requires password if MINDFIELD_ADMIN_PASS is set."""
        try:
            env_pass = os.environ.get('MINDFIELD_ADMIN_PASS')
            if env_pass:
                pw = simpledialog.askstring("Admin Password", "Enter external admin password:", show='*')
                if pw is None:
                    return
                if pw != env_pass:
                    messagebox.showerror("Access Denied", "Incorrect admin password")
                    return
            else:
                # No environment password configured — warn but allow
                if not messagebox.askyesno("No Admin Password", "No external admin password is configured (MINDFIELD_ADMIN_PASS). Continue and enable External Admin mode without a password?"):
                    return

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

            # When in self-admin, hide sensitive displays
            if mode == 'self':
                try:
                    # Mask stats and coherence
                    self.stats_label.config(text="Statistics hidden in Self-Admin Mode")
                    self.coherence_label.config(text="Coherence hidden in Self-Admin Mode")
                except Exception:
                    pass

                # Mask participant labels if visible
                try:
                    for addr, lbl in getattr(self, 'participant_labels', {}).items():
                        lbl.config(text="Hidden (self-admin)")
                except Exception:
                    pass
            else:
                # External admin — restore placeholders; live updates will repopulate
                try:
                    self.stats_label.config(text="Waiting to start...")
                    self.coherence_label.config(text="")
                    # If there are participant labels, mark as waiting for data
                    for addr, lbl in getattr(self, 'participant_labels', {}).items():
                        lbl.config(text="-- waiting --")
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Error", f"Could not set admin mode: {e}")

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

                    bt = self._get_bluetooth_state()

                    self._onboard_sdr_label.config(text=f"SDR: {'available' if s else 'not available'}")
                    self._onboard_bt_label.config(text=f"Bluetooth: {bt if bt else 'unknown'}")

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
        """Return 'blocked' or 'unblocked' or None on error."""
        try:
            res = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True)
            out = res.stdout.lower() + res.stderr.lower()
            if "soft blocked: yes" in out or "blocked: yes" in out:
                return "blocked"
            if "soft blocked: no" in out or "blocked: no" in out:
                return "unblocked"
            return None
        except Exception:
            return None

    def toggle_bluetooth(self):
        """Toggle bluetooth: try DBus/BlueZ first, then fall back to rfkill.

        Runs in a background thread so the UI doesn't block. Uses dbus-python
        if available to toggle the Adapter1.Powered property. If that fails
        (missing lib or permission), falls back to calling `rfkill`.
        """
        def worker():
            self.status_bar.config(text="Toggling Bluetooth...")

            # Try BlueZ via dbus-python first (preferred for multi-user setups)
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
                            powered = props.Get('org.bluez.Adapter1', 'Powered')
                            props.Set('org.bluez.Adapter1', 'Powered', dbus.Boolean(not powered))
                            new_state = 'on' if not powered else 'off'
                            self.status_bar.config(text=f"Bluetooth {new_state} (via BlueZ)")
                            messagebox.showinfo('Bluetooth', f'Bluetooth turned {new_state} via BlueZ')
                            # Update LED
                            self._set_led(self.bt_led, 'on' if new_state == 'on' else 'off')
                            return
                except Exception as e:
                    # DBus attempt failed -> fall back
                    print(f"DBus toggle failed: {e}")

            # Fall back to rfkill
            state = self._get_bluetooth_state()
            try:
                if state == "blocked":
                    cmd = ["rfkill", "unblock", "bluetooth"]
                    action = "unblocked"
                elif state == "unblocked":
                    cmd = ["rfkill", "block", "bluetooth"]
                    action = "blocked"
                else:
                    cmd = ["rfkill", "unblock", "bluetooth"]
                    action = "unblocked"

                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    self.status_bar.config(text=f"Bluetooth {action}")
                    messagebox.showinfo("Bluetooth", f"Bluetooth {action} successfully.")
                    # Update LED
                    self._set_led(self.bt_led, 'on' if action == 'unblocked' else 'off')
                else:
                    self.status_bar.config(text="Bluetooth toggle failed")
                    messagebox.showwarning(
                        "Bluetooth Toggle Failed",
                        "Could not toggle Bluetooth. Ask an admin to install a polkit rule or run:\n\nsudo rfkill unblock bluetooth\n\nCommand output:\n" + (res.stderr or res.stdout)
                    )
            except Exception as e:
                self.status_bar.config(text="Bluetooth toggle error")
                messagebox.showerror("Error", f"Bluetooth toggle failed: {e}")

        threading.Thread(target=worker, daemon=True).start()

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
