import secrets
import time
from collections import deque
import threading
import hmac
import hashlib

class RNGCollector:
    def __init__(self):
        self.bits = deque(maxlen=100000)
        self.baseline_bits = deque(maxlen=100000)
        self.running = False
        self.markers = []
        self.thread = None
        self.mode = "experiment"  # "experiment" or "baseline"
        self._lock = threading.Lock()
        self._drbg = None
        
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
            with self._lock:
                if self._drbg is not None:
                    # generate one bit from DRBG to mix deterministically from seed
                    bit = self._drbg.get_bits(1)
                else:
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

    # --- DRBG support ---
    class _HMAC_DRBG:
        """Minimal HMAC-DRBG (SHA-256) implementation for local bit generation.

        This follows the standard HMAC-DRBG update/generate pattern in a
        compact form. It's intended only to provide a seeded, auditable
        generator to mix SDR entropy into the collector. For cryptographic
        uses, prefer a vetted library.
        """
        def __init__(self, seed_material: bytes):
            self._K = b"\x00" * 32
            self._V = b"\x01" * 32
            self._hash = hashlib.sha256
            self._update(seed_material)

        def _hmac(self, key, data):
            return hmac.new(key, data, self._hash).digest()

        def _update(self, provided_data: bytes = b""):
            self._K = self._hmac(self._K, self._V + b"\x00" + provided_data)
            self._V = self._hmac(self._K, self._V)
            if provided_data:
                self._K = self._hmac(self._K, self._V + b"\x01" + provided_data)
                self._V = self._hmac(self._K, self._V)

        def generate(self, nbytes: int) -> bytes:
            out = b""
            while len(out) < nbytes:
                self._V = self._hmac(self._K, self._V)
                out += self._V
            return out[:nbytes]

        def get_bits(self, nbits: int) -> int:
            # Return integer containing nbits (<= 32) from the generator
            if nbits <= 0 or nbits > 32:
                raise ValueError("nbits must be between 1 and 32")
            # generate 4 bytes and extract top bits
            b = self.generate(4)
            val = int.from_bytes(b, 'big')
            return val & ((1 << nbits) - 1)

    def seed_rng(self, seed_bytes: bytes):
        """Seed an internal DRBG with provided seed material.

        Thread-safe: will replace the current DRBG used to generate bits
        inside the collector thread. The DRBG is used only if set; if
        seed_bytes is None or empty, the DRBG is not created.
        """
        if not seed_bytes:
            return
        with self._lock:
            try:
                self._drbg = self._HMAC_DRBG(seed_bytes)
            except Exception:
                # If DRBG instantiation fails, leave _drbg as None
                self._drbg = None
