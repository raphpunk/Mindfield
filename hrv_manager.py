import asyncio
from bleak import BleakScanner, BleakClient
import threading
import queue
import struct
import numpy as np
import time

class HRVDeviceManager:
    def __init__(self):
        self.devices = {}
        self.active_clients = {}
        self.coherence_queue = queue.Queue()
        
        # Standard BLE Heart Rate UUIDs
        self.HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
        self.HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
        
        # Device patterns to recognize
        self.device_patterns = [
            "Polar", "H808S", "H10", "H9", "OH1",
            "Garmin", "HRM-Dual", "HRM-Pro", "HRM-Run",
            "Wahoo", "TICKR", "TICKR X",
            "Suunto", "Smart Sensor",
            "Zephyr", "HxM",
            "RHYTHM", "Scosche",
            "HRM", "Heart Rate"  # Generic patterns
        ]
        
        self.rr_buffers = {}  # Store RR intervals per device
        
    async def scan_devices(self, timeout=10):
        devices = await BleakScanner.discover(timeout=timeout)
        hrv_devices = []
        
        for d in devices:
            if d.name:
                # Check if it matches any known patterns
                for pattern in self.device_patterns:
                    if pattern.lower() in d.name.lower():
                        hrv_devices.append({
                            'name': d.name,
                            'address': d.address,
                            'rssi': d.rssi
                        })
                        break
                        
        # Sort by signal strength
        hrv_devices.sort(key=lambda x: x['rssi'], reverse=True)
        return hrv_devices
    
    def connect_devices(self, selected_addresses):
        for addr in selected_addresses:
            self.rr_buffers[addr] = deque(maxlen=120)  # 2 minutes of RR data
            thread = threading.Thread(target=self._run_device, args=(addr,))
            thread.daemon = True
            thread.start()
    
    def _run_device(self, address):
        asyncio.run(self._monitor_device(address))
    
    async def _monitor_device(self, address):
        try:
            async with BleakClient(address) as client:
                print(f"Connected to {address}")
                
                def handler(sender, data):
                    hr_data = self._parse_hr_data(data, address)
                    if hr_data:
                        self.coherence_queue.put(hr_data)
                
                # Start notifications
                await client.start_notify(self.HR_MEASUREMENT_UUID, handler)
                
                # Keep connection alive
                while client.is_connected:
                    await asyncio.sleep(1)
                    
        except Exception as e:
            print(f"Device {address} error: {e}")
            self.coherence_queue.put({
                'device': address[-5:],
                'error': str(e),
                'coherence': 0
            })
    
    def _parse_hr_data(self, data, address):
        """Parse heart rate data according to BLE spec"""
        if len(data) < 2:
            return None
            
        # First byte contains flags
        flags = data[0]
        hr_format = flags & 0x01
        rr_present = (flags & 0x10) != 0
        
        # Parse heart rate
        if hr_format == 0:  # uint8
            hr = data[1]
            rr_offset = 2
        else:  # uint16
            hr = struct.unpack('<H', data[1:3])[0]
            rr_offset = 3
            
        # Parse RR intervals if present
        rr_intervals = []
        if rr_present:
            while rr_offset + 1 < len(data):
                rr = struct.unpack('<H', data[rr_offset:rr_offset+2])[0]
                rr_ms = rr * 1000 / 1024  # Convert to milliseconds
                rr_intervals.append(rr_ms)
                self.rr_buffers[address].append(rr_ms)
                rr_offset += 2
        
        # Calculate coherence from buffer
        coherence = self._calculate_coherence(address)
        
        return {
            'device': address[-5:],
            'hr': hr,
            'rr_count': len(rr_intervals),
            'coherence': coherence,
            'timestamp': time.time()
        }
    
    def _calculate_coherence(self, address):
        """Calculate HRV coherence (0-1 scale)"""
        rr_data = list(self.rr_buffers[address])
        
        if len(rr_data) < 10:
            return 0.0
            
        # Simple coherence: inverse of RR variance normalized
        rr_array = np.array(rr_data)
        
        # Calculate successive differences
        diff = np.diff(rr_array)
        
        # RMSSD (Root Mean Square of Successive Differences)
        rmssd = np.sqrt(np.mean(diff**2))
        
        # Normalize to 0-1 (lower RMSSD = higher coherence)
        # Typical RMSSD ranges from 20-100ms
        coherence = 1 / (1 + rmssd / 50)
        
        return min(max(coherence, 0), 1)  # Clamp to 0-1
    
    def get_all_coherence(self):
        """Get all recent coherence data"""
        data = []
        while not self.coherence_queue.empty():
            try:
                item = self.coherence_queue.get_nowait()
                if 'error' not in item:
                    data.append(item)
            except:
                break
        return data
    
    def disconnect_all(self):
        """Clean disconnect all devices"""
        # In real implementation, would track clients and disconnect
        pass

# Import deque at top if not already
from collections import deque
