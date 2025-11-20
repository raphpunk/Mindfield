# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]
- UI: Polished GUI using `ttk` styles and programmatic icons for main actions.
- Added onboarding dialog with quick SDR and Bluetooth checks.
- Added in-app Troubleshooting dialog showing `POLKIT_RULES.md` and `udev/52-rtl-sdr.rules`.
- RNG: Wired SDR-based entropy seeding with software fallback; added `sdr_rng.py`.
- Added thread-safe seeding to `RNGCollector` and improved session timing/countdown in GUI.
- Added tooltips, menu bar, status LEDs (RNG/BT/SDR), and duration presets.
- Added `scripts/setup-env.sh`, `requirements.txt`, and a `udev` rule for RTL-SDR.

## [v0.1] - initial
- Initial import / baseline project state.
