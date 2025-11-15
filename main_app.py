```python
import tkinter as tk
from tkinter import messagebox, filedialog
import threading
import asyncio
from datetime import datetime
import csv
import json
from hrv_manager import HRVDeviceManager
from rng_collector import RNGCollector
from group_session import GroupSessionManager

class ConsciousnessLab:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("mindfield-core")
        self.root.geometry("650x650")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Core components
        self.hrv_manager = HRVDeviceManager()
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
        # Title
        tk.Label(self.root, text="Consciousness Field Lab", 
                font=("Arial", 18, "bold")).pack(pady=10)
        
        # HRV Section
        hrv_frame = tk.LabelFrame(self.root, text="HRV Devices", padx=15, pady=10)
        hrv_frame.pack(fill="x", padx=20, pady=5)
        
        scan_btn = tk.Button(hrv_frame, text="Scan Devices", command=self.scan_devices,
                           bg="#3498db", fg="white", width=15)
        scan_btn.pack(pady=5)
        
        self.device_list = tk.Frame(hrv_frame)
        self.device_list.pack(fill="both", expand=True)
        
        # Stats Display
        stats_frame = tk.LabelFrame(self.root, text="Live Statistics", padx=15, pady=10)
        stats_frame.pack(fill="x", padx=20, pady=5)
        
        self.mode_label = tk.Label(stats_frame, text="Mode: IDLE", 
                                 font=("Arial", 12, "bold"), fg="#7f8c8d")
        self.mode_label.pack()
        
        self.stats_label = tk.Label(stats_frame, text="Waiting to start...", 
                                  font=("Arial", 14))
        self.stats_label.pack(pady=5)
        
        self.effect_label = tk.Label(stats_frame, text="", font=("Arial", 11))
        self.effect_label.pack()
        
        self.coherence_label = tk.Label(stats_frame, text="", font=("Arial", 11))
        self.coherence_label.pack()
        
        # Participant Display (for group sessions)
        self.participant_frame = tk.LabelFrame(self.root, text="Active Participants", padx=15, pady=10)
        # Don't pack initially
        
        self.participant_labels = {}
        
        # Control Panel
        control_frame = tk.Frame(self.root)
        control_frame.pack(pady=15)
        
        self.baseline_btn = tk.Button(control_frame, text="Run Baseline", 
                                    command=lambda: self.toggle_session("baseline"),
                                    bg="#9b59b6", fg="white", width=18, height=2)
        self.baseline_btn.pack(side="left", padx=5)
        
        self.experiment_btn = tk.Button(control_frame, text="Start Experiment",
                                      command=lambda: self.toggle_session("experiment"),
                                      bg="#27ae60", fg="white", width=18, height=2)
        self.experiment_btn.pack(side="left", padx=5)
        
        # Quick Actions
        action_frame = tk.Frame(self.root)
        action_frame.pack()
        
        tk.Button(action_frame, text="Mark Intention", command=self.mark_intention,
                bg="#e74c3c", fg="white", width=15).pack(side="left", padx=3)
        
        tk.Button(action_frame, text="Group Session", command=self.start_group_session,
                bg="#3498db", fg="white", width=15).pack(side="left", padx=3)
        
        tk.Button(action_frame, text="Export Session", command=self.export_session,
                bg="#34495e", fg="white", width=15).pack(side="left", padx=3)
        
        # Status Bar
        self.status_bar = tk.Label(self.root, text="Ready", bd=1, 
                                 relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
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
            
            # Update UI
            if mode == "baseline":
                self.mode_label.config(text="Mode: BASELINE", fg="#9b59b6")
                self.baseline_btn.config(text="Stop Baseline", bg="#e74c3c")
                self.experiment_btn.config(state="disabled")
            else:
                self.mode_label.config(text="Mode: EXPERIMENT", fg="#27ae60")
                self.experiment_btn.config(text="Stop Experiment", bg="#e74c3c")
                self.baseline_btn.config(state="disabled")
                
        else:
            self.stop_session()
            
    def stop_session(self):
        self.running = False
        self.rng_collector.stop()
        
        # Reset UI
        self.mode_label.config(text="Mode: IDLE", fg="#7f8c8d")
        self.baseline_btn.config(text="Run Baseline", bg="#9b59b6", state="normal")
        self.experiment_btn.config(text="Start Experiment", bg="#27ae60", state="normal")
        
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
                'markers': self.rng_collector.markers,
                'raw_bits': list(self.rng_collector.bits)[-10000:]  # Last 10k bits
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
                    for m in self.rng_collector.markers:
                        writer.writerow([m['timestamp'], m['event'], m['bit_index']])
                        
        # Save group metadata if group session
        if self.current_session_type == "group" and self.group_manager:
            self.group_manager.save_session_metadata(filepath)
            
        messagebox.showinfo("Exported", f"Session data saved to {filepath}")
        
    def show_error(self, message):
        messagebox.showerror("Error", message)
        self.status_bar.config(text="Error occurred")
        
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
