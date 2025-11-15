import secrets
import time
from collections import deque
import threading

class RNGCollector:
    def __init__(self):
        self.bits = deque(maxlen=100000)
        self.baseline_bits = deque(maxlen=100000)
        self.running = False
        self.markers = []
        self.thread = None
        self.mode = "experiment"  # "experiment" or "baseline"
        
    def start(self, mode="experiment"):
        self.running = True
        self.mode = mode
        self.thread = threading.Thread(target=self._collect)
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
        
    def _collect(self):
        while self.running:
            bit = secrets.randbits(1)
            if self.mode == "baseline":
                self.baseline_bits.append(bit)
            else:
                self.bits.append(bit)
            time.sleep(0.01)
    
    def mark_event(self, event_type, coherence_data=None):
        self.markers.append({
            'timestamp': time.time(),
            'bit_index': len(self.bits),
            'event': event_type,
            'coherence': coherence_data
        })
    
    def get_stats(self, window=1000):
        if self.mode == "baseline":
            bits_to_analyze = self.baseline_bits
        else:
            bits_to_analyze = self.bits
            
        bit_count = len(bits_to_analyze)
        if bit_count < 10:
            return {
                'mean': 0.5, 
                'z_score': 0, 
                'count': bit_count, 
                'markers': len(self.markers),
                'mode': self.mode
            }
        
        recent = list(bits_to_analyze)[-window:] if bit_count > window else list(bits_to_analyze)
        mean = sum(recent) / len(recent)
        z = (mean - 0.5) / (0.5 / (len(recent)**0.5))
        
        return {
            'mean': mean, 
            'z_score': z, 
            'count': bit_count,
            'markers': len(self.markers),
            'mode': self.mode
        }
    
    def get_baseline_comparison(self):
        """Compare current experiment to baseline"""
        if len(self.baseline_bits) < 100 or len(self.bits) < 100:
            return None
            
        baseline_mean = sum(self.baseline_bits) / len(self.baseline_bits)
        experiment_mean = sum(self.bits) / len(self.bits)
        
        # Effect size calculation
        effect = (experiment_mean - baseline_mean) / 0.5 * 100
        
        return {
            'baseline_mean': baseline_mean,
            'experiment_mean': experiment_mean,
            'effect_percent': effect,
            'baseline_bits': len(self.baseline_bits),
            'experiment_bits': len(self.bits)
        }
