# mindfield-core

> Where consciousness bends probability

A real-time consciousness field detection system that correlates heart rhythm coherence across multiple participants with quantum random number generation anomalies.

**Coherence Detection**: Automatic intention marking at high coherence thresholds
- **Baseline Comparison**: Run baseline sessions to compare against intention experiments
- **Session Recording**: Export all data to CSV for further analysis

## Installation

```bash
 # mindfield-core

 Consciousness-field experiment toolkit: real-time HRV coherence monitoring coupled with RNG collection and analysis.

 This application is research / experimental software intended to record HRV coherence across one or more devices, collect random bits (software fallback or optional RTL-SDR source), and correlate intention markers with RNG deviations.

 ## Goals
 - Capture HRV coherence in real time and record synchronized snapshots for RNG correlation.
 - Provide a compact GUI for running baseline and experiment sessions with exportable data.
 - Offer optional SDR-based entropy with robust handling and in-app troubleshooting.

 ## Prerequisites
 - Python 3.8+
 - A working display (X11 / Wayland) to run the Tkinter GUI
 - Bluetooth adapter and compatible HRV devices (Polar H9/H10, many chest straps)

 Optional for SDR entropy
 - RTL-SDR USB dongle (Nooelec, Realtek RTL2832U + R820T)

 ## Quick setup

 1. Clone and create a virtual environment:

 ```bash
 git clone https://github.com/raphpunk/Mindfield.git
 cd Mindfield
 python3 -m venv .venv
 source .venv/bin/activate
 pip install --upgrade pip
 pip install -r requirements.txt
 ```

 2. Run the GUI:

 ```bash
 python3 main_app.py
 ```

 ## Basic workflow
 1. Click "Scan Devices" to find HRV monitors and connect.
 2. Run a short baseline (recommended) using "Run Baseline".
 3. Start an experiment session; press "Mark Intention" when appropriate.
 4. Export session data (CSV/JSON) for analysis.

 ## Key files
 - `main_app.py` — GUI and session orchestration
 - `hrv_manager.py` — BLE HRV device handling
 - `rng_collector.py` — bit collection, DRBG support, HRV snapshot recording
 - `sdr_rng.py` — optional RTL-SDR backed entropy provider

 ## SDR (RTL-SDR) setup and troubleshooting

 The SDR path is optional. If present, the app will try to use it as an entropy source; otherwise it falls back to the software RNG.

 Install system packages (Debian/Ubuntu):

 ```bash
 sudo apt-get update
 sudo apt-get install -y rtl-sdr librtlsdr-dev librtlsdr0
 ```

 Grant non-root access (recommended):

 ```bash
 sudo cp udev/52-rtl-sdr.rules /etc/udev/rules.d/52-rtl-sdr.rules
 sudo udevadm control --reload-rules
 sudo udevadm trigger
 sudo usermod -aG plugdev $USER
 # Log out and log back in (or run `newgrp plugdev`)
 ```

 Quick SDR test (verbose):

 ```bash
 source .venv/bin/activate
 python sdr_rng.py -n 32 --verbose
 ```

 ### Common SDR issues and tips
 - If `rtl_test -t` reports `[R82XX] PLL not locked!`:
	 - Try a different USB port (avoid unpowered hubs).
	 - Ensure an antenna is connected.
	 - Try lower sample rates / smaller read buffers (the app does this automatically when initializing SDR).
	 - Unload conflicting DVB kernel modules (the app includes an "Apply Driver Fixes" helper under Help → Troubleshooting that can unload modules and install a udev rule and modprobe blacklist; it backs up files and supports undo).

 ## New in-app tools
 - Troubleshooting dialog (Help → Troubleshooting):
	 - Run `rtl_test -t` as root from the GUI and inspect output.
	 - Apply Driver Fixes: unload conflicting kernel modules and optionally install udev/modprobe files (creates `.bak` backups).
	 - Undo Driver Fixes: restore backups or remove created files.

 ## Bluetooth and HRV
 - Ensure your HRV sensor is in pairing mode and not connected to other apps.
 - If Bluetooth scanning fails, try enabling the service and checking `rfkill`:

 ```bash
 sudo systemctl enable --now bluetooth
 rfkill list
 ```

 ## Export and analysis
 - Use the Export Session control to save CSV or JSON containing bits, markers, HRV snapshots, and metadata.
 - For external analysis, tools like Python/pandas or R can ingest the exported files for statistical testing.

 ## Development notes
 - Optional Python packages (recommended): `numpy`, `pyrtlsdr`, `matplotlib`, `bleak`.
 - If you don't need SDR features, you can omit `pyrtlsdr` and `numpy` and the GUI will fall back to software RNG automatically.

 ## Contributing
 Contributions welcome. Please open issues or PRs for device support, UI improvements, or analysis tools.

 ## License
 MIT — see `LICENSE`

 ## Contact
 For questions or collaboration: open an issue or email the repo owner.
