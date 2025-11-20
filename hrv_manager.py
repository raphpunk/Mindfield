import asyncio
import subprocess
from datetime import datetime
from queue import Queue
import threading
from bleak import BleakClient, BleakScanner
from typing import Dict, List, Optional
import numpy as np

class HRVDeviceManager:
    def __init__(self, coherence_queue: Queue):
        self.coherence_queue = coherence_queue
        self.device_patterns = ['Polar', 'Wahoo', '808S', 'HRM', 'Heart Rate']
        self.active_devices = {}
        self.monitor_tasks = {}
        self.running = False
        self.latest_coherence = []
        self._async_loop = None
        self._thread = None
        
        # BLE UUIDs
        self.HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
        self.HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"
        
    async def scan_devices(self):
        """Scan for available HRV devices"""
        devices = await BleakScanner.discover(timeout=5)
        return [{'name': d.name, 'address': d.address, 'rssi': getattr(d, 'rssi', -99)} 
                for d in devices if d.name and any(p in d.name for p in self.device_patterns)]
    
    def connect_devices(self, addresses):
        """Connect to selected devices"""
        for addr in addresses:
            self.active_devices[addr] = "Connecting..."
            
        # If async loop not running, start it
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self._thread.start()
            
    def _run_async_loop(self):
        """Run the async event loop in a separate thread"""
        self._async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_until_complete(self._async_main())
        
    async def _async_main(self):
        """Main async loop"""
        self.running = True
        
        # Start monitors for all active devices
        for addr in list(self.active_devices.keys()):
            task = asyncio.create_task(self._monitor_device(addr))
            self.monitor_tasks[addr] = task
            
        # Keep running until stopped
        while self.running:
            await asyncio.sleep(1)
            
    async def _monitor_device(self, address):
        """Monitor a specific device with resilient connection"""
        device_name = self.active_devices.get(address, "Unknown")
        is_808s = "808S" in device_name or "808S" in str(address)
        max_retries = 5 if is_808s else 3
        
        for attempt in range(max_retries):
            try:
                # Force clean state for 808S
                if is_808s and attempt > 0:
                    print(f"Resetting BLE for {address}...")
                    subprocess.run(["sudo", "hciconfig", "hci0", "reset"], capture_output=True)
                    await asyncio.sleep(2)
                    
                client = BleakClient(address, timeout=30 if is_808s else 10)
                await client.connect()
                
                # Verify services available
                if not client.services:
                    print(f"No services found for {address}")
                    await client.disconnect()
                    continue
                    
                print(f"Connected to {address} (attempt {attempt + 1})")
                self.active_devices[address] = "Connected"
                
                # Set up notification handler
                def handler(sender, data):
                    hr_data = self._parse_hr_data(data, address)
                    if hr_data:
                        self.coherence_queue.put(hr_data)
                        self.latest_coherence.append(hr_data)
                        if len(self.latest_coherence) > 100:
                            self.latest_coherence.pop(0)
                        
                await client.start_notify(self.HR_MEASUREMENT_UUID, handler)
                
                # Keep connection alive
                while client.is_connected and self.running:
                    await asyncio.sleep(1)
                    
                await client.stop_notify(self.HR_MEASUREMENT_UUID)
                await client.disconnect()
                
            except Exception as e:
                print(f"Device {address} error: {e}")
                if attempt == max_retries - 1:
                    self.coherence_queue.put({
                        'timestamp': datetime.now().timestamp(),
                        'device': address,
                        'error': str(e),
                        'coherence': 0,
                        'heart_rate': 0,
                        'rr_intervals': []
                    })
                    
        # Cleanup on disconnect
        self.active_devices.pop(address, None)
        self.monitor_tasks.pop(address, None)
        
    def _parse_hr_data(self, data, address) -> Optional[Dict]:
        """Parse BLE heart rate measurement data"""
        try:
            flags = data[0]
            hr_format = flags & 0x01
            
            # Parse heart rate
            if hr_format == 0:
                hr = data[1]
                rr_offset = 2
            else:
                hr = int.from_bytes(data[1:3], 'little')
                rr_offset = 3
                
            # Parse RR intervals
            rr_intervals = []
            if flags & 0x10:  # RR interval data present
                i = rr_offset
                while i < len(data) - 1:
                    rr_raw = int.from_bytes(data[i:i+2], 'little')
                    rr_ms = rr_raw * 1000 / 1024  # Convert to milliseconds
                    rr_intervals.append(rr_ms)
                    i += 2
                    
            # Calculate simple coherence
            coherence = 0
            if len(rr_intervals) >= 3:
                rr_diff = np.diff(rr_intervals)
                if np.std(rr_diff) > 0:
                    coherence = 1 / (1 + np.std(rr_diff) / 100)
                    
            return {
                'timestamp': datetime.now().timestamp(),
                'device': address,
                'heart_rate': hr,
                'rr_intervals': rr_intervals,
                'coherence': coherence
            }
            
        except Exception as e:
            print(f"Parse error for {address}: {e}")
            return None
            
    def get_all_coherence(self) -> List[Dict]:
        """Return latest coherence data"""
        return self.latest_coherence[-10:] if self.latest_coherence else []
    
    def get_active_devices(self) -> List[str]:
        """Return list of currently connected devices"""
        return list(self.active_devices.keys())
    
    def stop(self):
        """Stop all monitoring"""
        self.running = False
        if self._async_loop and self._thread:
            self._async_loop.call_soon_threadsafe(self._async_loop.stop)
            self._thread.join(timeout=5)
