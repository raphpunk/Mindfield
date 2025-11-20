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
        # Store HRV snapshots tied to bit indices for correlation analysis
        self.hrv_snapshots = deque(maxlen=100000)
        self.running = False
        self.markers = []
        self.thread = None
        self._sdr_stream_thread = None
        self._sdr_streaming = False
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
    
    def mark_event(self, event_type, coherence_data=None, meta=None):
        """Record a marker event with optional metadata.

        meta: optional dict with extra information (e.g., {'intent': 'Calm'}).
        """
        entry = {
            'timestamp': time.time(),
            'bit_index': len(self.bits),
            'event': event_type,
            'coherence': coherence_data
        }
        if meta is not None:
            entry['meta'] = meta
        self.markers.append(entry)

    def record_hrv_snapshot(self, hrv_sample: dict):
        """Record an HRV sample alongside the current bit index for later correlation.

        hrv_sample is expected to be the dict produced by HRVDeviceManager._parse_hr_data,
        containing at least 'timestamp', 'device', 'heart_rate', 'rr_intervals', 'coherence'.
        We augment it with the current bit_index for correlation analysis.
        """
        try:
            if not isinstance(hrv_sample, dict):
                return False
            entry = dict(hrv_sample)
            entry.setdefault('timestamp', time.time())
            # Attach the bit index at the time this sample was observed
            entry['bit_index'] = len(self.bits)
            self.hrv_snapshots.append(entry)
            return True
        except Exception:
            return False
    
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

    # --- SDR streaming support ---
    def start_sdr_stream(self, sdr_provider, sample_to_bits_fn=None):
        """Start a background thread that continuously consumes raw SDR blocks
        from `sdr_provider` and pushes bits into the collector.

        - `sdr_provider` should be a callable that returns bytes (raw whitened
          or raw sample bytes) each call, or an object with `get_random_bytes(n)`
          method.
        - `sample_to_bits_fn` optional function(bytes)->iterable_of_bits. If not
          provided, bytes will be unpacked by their bit values (MSB-first).
        """
        if self._sdr_streaming:
            return

        def _default_unpack(bts):
            for byte in bts:
                for i in range(8):
                    # yield LSB first for compactness
                    yield (byte >> i) & 1

        unpack = sample_to_bits_fn or _default_unpack

        def _worker():
            self._sdr_streaming = True
            try:
                while self._sdr_streaming:
                    try:
                        # prefer provider.get_random_bytes if present
                        if callable(sdr_provider):
                            raw = sdr_provider()
                        else:
                            raw = sdr_provider.get_random_bytes(1024)
                        if not raw:
                            # brief sleep to avoid busy loop on intermittent failures
                            time.sleep(0.2)
                            continue

                        with self._lock:
                            for bit in unpack(raw):
                                if self.mode == "baseline":
                                    self.baseline_bits.append(bit)
                                else:
                                    self.bits.append(bit)
                        # small throttle to allow UI responsiveness
                        time.sleep(0.01)
                    except Exception:
                        # On SDR errors, pause briefly and continue
                        time.sleep(0.5)
            finally:
                self._sdr_streaming = False

        self._sdr_stream_thread = threading.Thread(target=_worker, daemon=True)
        self._sdr_stream_thread.start()

    def stop_sdr_stream(self):
        """Stop the background SDR streaming thread if running."""
        try:
            self._sdr_streaming = False
            if self._sdr_stream_thread is not None:
                self._sdr_stream_thread.join(timeout=1)
        except Exception:
            pass

    def import_baseline_bits(self, bits_iterable):
        """Import baseline bits from an iterable of 0/1 values (or bytes).

        Accepts lists of ints, or bytes (will unpack to bits MSB-first).
        """
        try:
            # If bytes-like provided, unpack to bits
            if isinstance(bits_iterable, (bytes, bytearray)):
                for byte in bits_iterable:
                    for i in range(8):
                        self.baseline_bits.append((byte >> i) & 1)
                return True

            # Iterable of ints
            for v in bits_iterable:
                if v in (0, 1):
                    self.baseline_bits.append(int(v))
                else:
                    # ignore invalid values
                    continue
            return True
        except Exception:
            return False
