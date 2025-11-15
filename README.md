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
