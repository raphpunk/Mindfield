"""SDR-backed RNG with software fallback

Provides `get_random_bytes(n)` which will attempt to use an RTL-SDR
(RtlSdr via `pyrtlsdr`) as an entropy source, whiten the collected
samples with SHA-256, and return cryptographically-useful bytes.

If no SDR library/device is available or sampling fails, it falls back
to `secrets.token_bytes(n)`.

This file is intentionally dependency-light: it will *optionally* use
`pyrtlsdr` and `numpy` when available but will still work without them.

Usage:
    from sdr_rng import get_random_bytes
    b = get_random_bytes(32)

Notes:
- SDR extraction is treated as an entropy sensor. The output is whitened
  via repeated SHA-256 hashing of raw sample blocks. For high-assurance
  use, feed this into a tested CSPRNG (e.g., HMAC-DRBG) and run
  continuous-health tests.
- Adjust `samples_per_hash` to tune throughput vs. entropy sampling.
"""

import hashlib
import secrets
import struct
import time

# Optional dependencies
try:
    import numpy as np
    from rtlsdr import RtlSdr
except Exception:
    np = None
    RtlSdr = None


class SDRRNG:
    """SDR-backed RNG implementation.

    This implementation extracts the least-significant bits from I/Q
    samples and whiten them by hashing blocks with SHA-256. The
    `get_random_bytes` method will gather enough hashed output to
    satisfy the requested number of bytes.
    """

    def __init__(self, sample_rate=2.4e6, center_freq=100e6, gain='auto', samples_per_hash=65536):
        if RtlSdr is None or np is None:
            raise RuntimeError('pyrtlsdr or numpy not available')
        self.sample_rate = sample_rate
        self.center_freq = center_freq
        self.gain = gain
        # Number of complex samples to read per hash cycle (must be even-ish)
        self.samples_per_hash = int(samples_per_hash)

    def _collect_raw_bytes(self):
        """Read samples from the SDR and return raw bytes extracted from I/Q LSBs."""
        sdr = None
        try:
            sdr = RtlSdr()
            sdr.sample_rate = float(self.sample_rate)
            sdr.center_freq = float(self.center_freq)
            sdr.gain = self.gain

            samples = sdr.read_samples(self.samples_per_hash)
            # samples is a numpy array of complex64
            i = np.clip(np.round(np.real(samples) * 127.0), -128, 127).astype(np.int8)
            q = np.clip(np.round(np.imag(samples) * 127.0), -128, 127).astype(np.int8)

            # Extract 1 LSB from I and Q and interleave: i0 q0 i1 q1 ...
            i_bits = (i & 1).astype(np.uint8)
            q_bits = (q & 1).astype(np.uint8)

            bits = np.empty(i_bits.size + q_bits.size, dtype=np.uint8)
            bits[0::2] = i_bits
            bits[1::2] = q_bits

            # Pad to whole bytes
            pad = (-bits.size) % 8
            if pad:
                bits = np.pad(bits, (0, pad), 'constant', constant_values=0)

            # Pack bits into bytes (big-endian bit order within a byte)
            packed = np.packbits(bits.reshape(-1, 8), axis=1)
            # packed is shape (N,1) -> flatten
            out = packed.flatten().tobytes()
            return out
        finally:
            if sdr is not None:
                try:
                    sdr.close()
                except Exception:
                    pass

    def get_random_bytes(self, nbytes=32, max_cycles=16):
        """Return `nbytes` of whitened random data.

        The method repeatedly collects raw bytes from the SDR and hashes
        them with SHA-256 to produce whitened output. If SDR sampling
        fails, a RuntimeError is raised by this class and caller can
        decide fallback.
        """
        out = bytearray()
        counter = 0
        cycles = 0
        while len(out) < nbytes:
            raw = self._collect_raw_bytes()
            if not raw:
                raise RuntimeError('No raw bytes collected from SDR')
            # Incorporate a cycle counter to diversify hashes
            h = hashlib.sha256()
            h.update(struct.pack('>Q', int(time.time() * 1000) & ((1 << 64) - 1)))
            h.update(struct.pack('>I', counter))
            h.update(raw)
            digest = h.digest()
            out.extend(digest)
            counter += 1
            cycles += 1
            if cycles > max_cycles and len(out) < nbytes:
                # Prevent indefinite attempts
                raise RuntimeError('SDR RNG could not produce enough output')
        return bytes(out[:nbytes])


class SoftwareRNG:
    """Secure software RNG fallback using Python's `secrets` module."""

    def get_random_bytes(self, nbytes=32):
        return secrets.token_bytes(nbytes)


def is_sdr_available():
    """Return True if an RTL-SDR device appears available (pyrtlsdr present and instantiable)."""
    if RtlSdr is None or np is None:
        return False
    sdr = None
    try:
        sdr = RtlSdr()
        return True
    except Exception:
        return False
    finally:
        if sdr is not None:
            try:
                sdr.close()
            except Exception:
                pass


def get_random_bytes(nbytes=32, prefer_sdr=True, sdr_params=None, verbose=False):
    """Public helper: try SDR if available and preferred, else software RNG.

    - `nbytes`: number of bytes to return
    - `prefer_sdr`: if True, attempt to initialize and use SDRRNG
    - `sdr_params`: dict of params for SDRRNG constructor
    """
    sdr_params = sdr_params or {}
    if prefer_sdr and is_sdr_available():
        try:
            sdr = SDRRNG(**sdr_params)
            out = sdr.get_random_bytes(nbytes)
            if verbose:
                print("SDR: used RTL-SDR to collect entropy")
            return out
        except Exception as e:
            # If SDR initialization or sampling fails, fall back but optionally report
            if verbose:
                print(f"SDR RNG failed, falling back to software RNG: {e}")
    # Fallback
    if verbose:
        print("SDR: not available, using software RNG fallback")
    sw = SoftwareRNG()
    return sw.get_random_bytes(nbytes)


if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser(description='SDR-backed RNG with software fallback')
    p.add_argument('-n', '--bytes', type=int, default=32, help='number of bytes to produce')
    p.add_argument('--no-sdr', action='store_true', help='force software RNG')
    p.add_argument('--verbose', action='store_true', help='print whether SDR or fallback was used')
    args = p.parse_args()
    b = get_random_bytes(args.bytes, prefer_sdr=not args.no_sdr, verbose=args.verbose)
    print(b.hex())
