import tkinter as tk
from tkinter import messagebox
import json
from datetime import datetime

class GroupSessionManager:
    def __init__(self, parent, hrv_manager):
        self.parent = parent
        self.hrv_manager = hrv_manager
        self.device_assignments = {}
        
    def start_dialog(self, selected_devices):
        if not selected_devices:
            messagebox.showinfo("No Devices", "Select HRV devices first")
            return None
            
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title("Group Session Setup")
        self.dialog.geometry("400x500")
        self.dialog.grab_set()
        
        # Header
        tk.Label(self.dialog, text="Consciousness Group Session", 
                font=("Arial", 16, "bold")).pack(pady=10)
        
        tk.Label(self.dialog, text="Assign participants to devices:", 
                font=("Arial", 12)).pack(pady=5)
        
        # Device assignment section
        assign_frame = tk.LabelFrame(self.dialog, text="Participants", padx=10, pady=10)
        assign_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.entries = []
        for i, (addr, name) in enumerate(selected_devices):
            frame = tk.Frame(assign_frame)
            frame.pack(fill="x", pady=5)
            
            tk.Label(frame, text=f"{name}\n{addr[-5:]}", 
                    width=15, anchor="w").pack(side="left")
            
            name_entry = tk.Entry(frame, width=20)
            name_entry.insert(0, f"Participant {i+1}")
            name_entry.pack(side="left", padx=10)
            
            role_var = tk.StringVar(value="participant")
            tk.OptionMenu(frame, role_var, "participant", "facilitator", 
                         "observer").pack(side="left")
            
            self.entries.append((addr, name_entry, role_var))
        
        # Session settings
        settings_frame = tk.LabelFrame(self.dialog, text="Session Settings", padx=10, pady=10)
        settings_frame.pack(fill="x", padx=20, pady=10)
        
        tk.Label(settings_frame, text="Session Name:").grid(row=0, column=0, sticky="w")
        self.session_name = tk.Entry(settings_frame, width=25)
        self.session_name.insert(0, f"Group_{datetime.now().strftime('%H%M')}")
        self.session_name.grid(row=0, column=1, padx=5)
        
        tk.Label(settings_frame, text="Intention:").grid(row=1, column=0, sticky="w", pady=5)
        self.intention_entry = tk.Entry(settings_frame, width=25)
        self.intention_entry.grid(row=1, column=1, padx=5)
        
        # Buttons
        button_frame = tk.Frame(self.dialog)
        button_frame.pack(pady=20)
        
        tk.Button(button_frame, text="Start Session", command=self.confirm,
                 bg="#27ae60", fg="white", width=15, height=2).pack(side="left", padx=5)
        
        tk.Button(button_frame, text="Cancel", command=self.dialog.destroy,
                 bg="#95a5a6", fg="white", width=15, height=2).pack(side="left", padx=5)
        
        # Make dialog modal
        self.dialog.transient(self.parent)
        self.dialog.wait_window()
        
        return self.device_assignments
    
    def confirm(self):
        # Collect all assignments
        self.device_assignments = {
            'session_name': self.session_name.get(),
            'intention': self.intention_entry.get(),
            'timestamp': datetime.now().isoformat(),
            'participants': {}
        }
        
        for addr, name_entry, role_var in self.entries:
            participant_name = name_entry.get().strip()
            if participant_name:
                self.device_assignments['participants'][addr] = {
                    'name': participant_name,
                    'role': role_var.get(),
                    'device': addr
                }
        
        # Pass to HRV manager
        self.hrv_manager.device_names = {
            addr: data['name'] 
            for addr, data in self.device_assignments['participants'].items()
        }
        
        self.dialog.destroy()
    
    def save_session_metadata(self, filepath):
        """Save group session details"""
        with open(filepath.replace('.csv', '_group.json'), 'w') as f:
            json.dump(self.device_assignments, f, indent=2)
