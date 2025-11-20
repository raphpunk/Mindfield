"""Atmospheric/Quantum RNG helper.

Provides `get_random_bytes(n, prefer_online=True)` which attempts to fetch
quantum-random bytes from ANU QRNG (https://qrng.anu.edu.au/) and falls
back to local SDR-based RNG (`sdr_rng.get_random_bytes`) or `secrets.token_bytes`.

This module purposely avoids adding heavy dependencies by using the
standard library (`urllib.request`). Network access is optional; failures
fall back gracefully.
"""
from typing import Optional
import json
import time
import secrets
import urllib.request
import urllib.error

try:
    from sdr_rng import get_random_bytes as sdr_get_random_bytes
except Exception:
    sdr_get_random_bytes = None

ANU_QRNG_URL = "https://qrng.anu.edu.au/API/jsonI.php"


def _fetch_anu_bytes(n: int, timeout: float = 5.0) -> Optional[bytes]:
    """Fetch up to `n` bytes from ANU QRNG. Returns None on failure."""
    if n <= 0:
        return b""

    # ANU accepts length param; limit some large requests by chunking
    max_chunk = 1024
    parts = []
    remaining = n

    while remaining > 0:
        ask = min(remaining, max_chunk)
        qs = f"?length={ask}&type=uint8"
        url = ANU_QRNG_URL + qs
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                raw = resp.read()
                try:
                    obj = json.loads(raw.decode('utf-8'))
                except Exception:
                    return None
                # API returns {'type':'uint8','length':..., 'data': [...], 'success': true}
                data = obj.get('data')
                if not data or not isinstance(data, list):
                    return None
                parts.append(bytes(data))
                remaining -= len(data)
                # be polite
                time.sleep(0.05)
        except urllib.error.URLError:
            return None
        except Exception:
            return None

    return b"".join(parts)


def get_random_bytes(n: int, prefer_online: bool = True, timeout: float = 5.0) -> bytes:
    """Return `n` random bytes.

    Strategy:
    1. If `prefer_online` is True, try ANU QRNG over HTTPS.
    2. If that fails and an SDR-based RNG is available (`sdr_rng.get_random_bytes`), use it.
    3. Fallback to `secrets.token_bytes(n)`.
    """
    if n <= 0:
        return b""

    # New policy: prefer SDR as primary source unless caller asks otherwise.
    if sdr_get_random_bytes is not None:
        try:
            b = sdr_get_random_bytes(n, prefer_sdr=True)
            if b is not None and len(b) >= n:
                return b[:n]
        except Exception:
            # SDR failed; fall through to other sources
            pass

    # If SDR unavailable or failed, try ANU QRNG online as secondary
    try:
        b = _fetch_anu_bytes(n, timeout=timeout)
        if b is not None and len(b) >= n:
            return b[:n]
    except Exception:
        pass

    # Final fallback to secure software RNG
    return secrets.token_bytes(n)
