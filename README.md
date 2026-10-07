# Rok Tracker

## Summary

Open Source Rise of Kingdoms Stats Management Tool for the **PC client** (the scanner reads the game window and clicks with the mouse, no emulator or ADB needed). Track TOP X players in kingdom / alliance / honor leaderboard. Depending on what you scan the resulting spreadsheet will look different:

**Kingdom rankings:** Governor ID, Governor Name, Power, Kill Points, Ranged Points, T1-T5 Kills, Total Kills, T4+T5 Kills, Dead Troops, RSS Gathered, RSS Assistance, Helps and Alliance name.

**Honor, alliance and seed rankings:** Governor name and score only. Because the game doesn't guarantee name accuracy, a screenshot of the name is saved in addition.

This is a heavily modified version of the original tool from [nikolakis1919](https://github.com/nikolakis1919/RokTracker).

There are two ways of using the scanner:
- **Simple installation** — download the released `.exe`, no Python required. [Instructions below](#simple-installation).
- **Advanced installation** — clone the source and run with Python. [Instructions below](#advanced-installation).

## Breaking Changes (v6)

- **Config restructured:** The single `config.json` has been replaced with a `config/` folder containing multiple files for global settings, scanner presets, and GUI config. Version 5 configs are not compatible.
- **Unified scanner:** The individual `kingdom_scanner`, `alliance_scanner`, `honor_scanner`, and `seed_scanner` entry points have been merged into a single `scanner_console.py` and `scanner_ui.py`. The CLI presents an interactive menu to select the scan type.

## Latest Changes

- Unified scanner
- More config options
  - Options per scan type
  - UI positions no longer hard coded → now in `config/internal/*.json`
- Support for Acclaim
- New GUI with theme support

---

## Screenshots

<details>

<summary>Click to expand</summary>

| Light Theme | Dark Theme |
|-------------|------------|
| ![Kingdom Scanner (Light)](images/kingdom-scanner-gui-light.png) | ![Kingdom Scanner (Dark)](images/kingdom-scanner-gui-dark.png) |

</details>

---

## Installation

**Prerequisites:** Rise of Kingdoms PC client, Python 3.14+ ([download](https://www.python.org/downloads/)) or uv ([instructions](https://docs.astral.sh/uv/)), [tessdata](https://github.com/tesseract-ocr/tessdata). On Windows, [Build Tools for C++](https://visualstudio.microsoft.com/de/visual-cpp-build-tools/) may be required.

1. Place tessdata into `deps/tessdata/` (see [Folder Structure](#folder-structure))
2. Place the two copy-name icon pictures `copy_icon.png` and `copy_icon_white.png` into `assets/`
3. Set up the game window — [see below](#game-window-setup)
4. Install dependencies: `uv sync`
5. Run the scanner:
   - `uv run scanner_console.py` — CLI (select scan type interactively)
   - `uv run scanner_ui.py` — GUI (tabs for Kingdom and Rankings)

---

## Config Files

The `config/` folder contains these files:

| File | Purpose |
|------|---------|
| `config.json` | Global settings (game window title, click offset, wait timings) |
| `kingdom_defaults.json` | Default options for kingdom scans |
| `seed_defaults.json` | Default options for seed (quick) scans |
| `alliance_defaults.json` | Default options for alliance ranking scans |
| `honor_defaults.json` | Default options for honor ranking scans |
| `gui_config.json` | GUI settings (default theme) |

---

## Folder Structure

Only these directories need manual attention:

```
deps/
└── tessdata/
assets/
├── copy_icon.png
└── copy_icon_white.png
```

Everything else (`config/`, `_internal/`, source scripts) is either downloaded from the release or generated automatically. Scan results go into `scans_kingdom/`, `scans_alliance/`, `scans_honor/`, `scans_seed/`. Intermediate screenshots go into `temp_images/`.

---

## Features

### Kingdom Scanner

- Full kingdom ranking scan (all governors)
- Wrong kill detection: validates that kills match kill points; suspicious items are saved to `manual_review/` with `F` prefix and a warning is logged
- Kill reconstruction: option to try recovering wrong kill values; reconstructed items get `R` prefix in `manual_review/`
- Inactive account detection: accounts that can't be clicked are skipped automatically; screenshots can optionally be saved to `inactives/`
- Stats to scan can be selected (if only stats from 1st page are use 2nd and 3rd page will get skipped to make the scan faster)

### Ranking Scanner

- Full alliance ranking scan
- Full personal honor ranking scan
- Seed scan (from kingdom rankings)
- Names are approximate (game limitation); governor ID is not tracked

---

## Game Window Setup

- Run the game in **windowed mode** and keep the whole window visible on screen (not minimized, not covered by other windows) while scanning — the scanner takes screenshots of the screen area of the game.
- Use a **16:9 client area** (e.g. 1600x900). Every position in `config/internal/*.json` is written for 1600x900; other 16:9 sizes are scaled automatically. A different aspect ratio misaligns everything and is logged as a warning.
- The window title defaults to `Rise of Kingdoms` (partial matches work). Change it in the app or in `config/config.json` (`general.window_title`).
- If clicks/screenshots are consistently shifted vertically, set `general.y_offset` (in 1600x900 pixels) in `config/config.json`.
- **Press F10 at any time to abort a running scan immediately** — the mouse is controlled by the scanner, so this is the quickest way out. The "End scan" button finishes the current governor first.
- Don't touch the mouse while a scan runs.

### Checking positions

`uv run calibrate.py` takes a screenshot of the open game screen and saves it (plus a copy with every configured region, click position and list row drawn on it) into `calibration/`. Open a governor profile, the kill statistics popup or the More Info page, run it, and check that each green box sits on its text. Use `--config alliance|honor|seed` for the ranking scans.

Regions set to `0, 0, 0, 0` in `config/internal/kingdom.json` have not been measured for the PC client: that stat is skipped (and its column left out of the output) until you fill in a region. Currently that is `gathered` and the per tier killpoints (needed only to *reconstruct* wrong kills). The Alliance, Honor and Seed ranking configs have `"calibrated": false` and refuse to start until their positions and scroll drag are measured and the flag is set to `true`.

---

## Important Notes

### Scan Preparation

- Your active character must be in **Home Kingdom** to scan only your kingdom (otherwise KvK players from other kingdoms are included)
- Start the scanner from the **top** of the relevant ranking page with the game window visible — don't scroll during the scan
- **Kingdom scan only:** Your rank must be lower than the number of players you want to scan (e.g., can't scan top 100 if you're ranked 85). Use an alt account
- **Kingdom scan only:** "Resume scan" starts from the governor currently visible on screen (the 4th one down)
- Game Language must be **English** — other languages break inactive governor detection
- Chinese characters may not render in CMD but are visible in the final export file
- The scanner uses your mouse and clipboard (to read names), so don't use the PC for anything else while it runs
- **Important:** Always copy the scan `.xlsx` file when finished — on the next scan there is a (small) chance it gets overwritten

### Configuration


## Getting Help

The best way to get help is on Discord — my username is **cyrexxis** (same as my GitHub username). I'm on the official RoK Server and the Chisgule server.

To get help faster, follow these rules:
1. Send a message request explaining your problem
2. Include your `scanner.log` file — "it doesn't work" without logs won't get answered
3. There's no guaranteed response time since this is a free-time project

[GitHub Discussions](https://github.com/Cyrexxis/RokTracker/discussions) is another option — others may benefit from your troubleshooting
