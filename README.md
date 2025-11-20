# mindfield-core

> Where consciousness bends probability

A real-time consciousness field detection system that correlates heart rhythm coherence across multiple participants with quantum random number generation anomalies.

## Features

- **Multi-HRV Support**: Connect unlimited Polar H9/H10 or H808S devices simultaneously
- **Hardware RNG**: Cryptographically secure random bit generation at 100Hz
- **Real-time Analysis**: Live statistical deviation tracking with z-score calculations
- **Coherence Detection**: Automatic intention marking at high coherence thresholds
- **Baseline Comparison**: Run baseline sessions to compare against intention experiments
- **Session Recording**: Export all data to CSV for further analysis

## Installation

```bash
git clone https://github.com/yourusername/mindfield-core.git
cd mindfield-core
pip install -r requirements.txt
```

### Requirements
- Python 3.8+
- Bluetooth adapter
- Compatible HRV devices (Polar H9/H10, H808S)

### Dependencies
```
tkinter
bleak>=0.21.0
numpy>=1.24.0
matplotlib>=3.7.0
```

## Quick Start

```bash
python3 main_app.py
```

1. Click "Scan for Devices" to find HRV monitors
2. Select which devices to use
3. Run a 5-minute baseline first
4. Start experiment and focus intention on affecting the RNG
5. Mark significant moments with "Mark Intention"
6. Compare results against baseline

## Usage Modes

### Baseline Mode
Establishes your personal RNG baseline without intention:
- 5-minute collection period
- No intention/influence attempts
- Provides comparison data for experiments

### Experiment Mode
Active consciousness-field interaction:
- Real-time z-score display
- Automatic marking at coherence > 0.8
- Effect size calculation vs baseline
- Multi-person coherence averaging

## Data Output

CSV files contain:
- Timestamp, mean, z-score, bit count
- Intention markers with coherence values
- Baseline comparison statistics
- Individual session metadata

## Statistical Significance

- **Z > 2.0**: Significant deviation (p < 0.05)
- **Z > 3.0**: Highly significant (p < 0.003)
- **Effect Size**: Percentage shift from baseline mean

## Architecture

```
mindfield-core/
├── main_app.py         # GUI and main application logic
├── hrv_manager.py      # Bluetooth HRV device management
├── rng_collector.py    # Hardware RNG and statistics
└── README.md
```

## Research Applications

- Consciousness-field coupling experiments
- Group coherence studies
- Intention-based RNG influence
- Presentiment research
- Global consciousness events

## Troubleshooting

**Bluetooth not available**: 
```bash
sudo systemctl start bluetooth
sudo apt install bluez  # Linux only
```

**No HRV devices found**: Ensure devices are in pairing mode and not connected to other apps

**Permission errors**: May need to run with sudo on Linux for BLE scanning

## SDR (Nooelec / RTL-SDR) Setup (optional)

This project can use a Nooelec/RTL-SDR device as an entropy source for seeding the RNG. The SDR path is optional — the software RNG fallback will be used if an SDR is not available.

1. Install system packages (Debian/Ubuntu):

```bash
sudo apt-get update
sudo apt-get install -y rtl-sdr librtlsdr-dev librtlsdr0
```

2. Install Python dependencies in a virtual environment (recommended):

```bash
./scripts/setup-env.sh
source .venv/bin/activate
```

3. Allow non-root access to the SDR device (one-time admin step):

```bash
sudo cp udev/52-rtl-sdr.rules /etc/udev/rules.d/52-rtl-sdr.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG plugdev $USER
# Log out and log back in (or run `newgrp plugdev`) to apply group changes.
```

4. Test SDR availability and collect a seed (verbose):

```bash
source .venv/bin/activate
python sdr_rng.py -n 32 --verbose
```

If the script prints `SDR: used RTL-SDR to collect entropy`, the SDR path is working.

Notes:
- The SDR-based entropy is whitened (SHA-256) before use and is combined into an internal DRBG. For production/high-assurance use, follow NIST SP800-90 recommendations and run continuous health tests.
- See `POLKIT_RULES.md` for guidance on polkit rules if you want to manage Bluetooth via DBus without sudo.

## Contributing

This is experimental research software. Pull requests welcome for:
- Additional HRV device support
- Alternative RNG sources (hardware/Arduino)
- Advanced statistical analysis
- Real-time visualization improvements

## License

MIT License - See LICENSE file

## Acknowledgments

Inspired by the Global Consciousness Project and the intersection of consciousness research with quantum mechanics.

---

*For questions or collaboration: [your-email]*
