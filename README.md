# Idoru LED Matrix Dashboard Renderer 🎴

Pixel-art dashboard renderer for the iDotMatrix 16×32 BLE LED display. Pulls health data from InfluxDB, renders it as tiny 16×32 pixel PNGs, and pushes them to the display via BLE.

## Dashboards

| # | Dashboard | Type | Data Source |
|---|-----------|------|-------------|
| 1 | Avg Power (W) | Bar graph | `cycling_power` Avg, `active_energy` |
| 2 | Calories In vs Out | Line comparison | `dietary_energy`, `active_energy` + `basal_energy_burned` |
| 3 | Net Calorie Balance | Bar graph | Calories in − calories out |
| 4 | Daily Protein | Bar graph | `protein` qty |
| 5 | Sleep Duration | Bar graph | `sleep_analysis` totalSleep |

## Architecture

```
InfluxDB → fetch_dashboard_data.py → render_pixel_charts.py → push_to_display.py
                                                     ↓
                                            dashboard_N.png (16×32)
```

## Files

- `src/fetch.py` — pull health data from InfluxDB
- `src/render.py` — Pillow-based pixel chart renderer (bar, line, gauge)
- `src/display.py` — BLE push to iDotMatrix display
- `src/cycle.py` — main loop: fetch → render → push → sleep
- `scripts/influx_query.sh` — symlink to health-query skill

## Usage

```bash
# Run one full cycle
./src/cycle.py

# Run continuously with 15s pause between cycles
./src/cycle.py --loop --pause 15

# Render charts only (no display push)
./src/render.py --output-dir ./output/

# Push a specific image
./src/display.py ./output/dashboard_1_power.png
```

## Requirements

- Python 3.10+
- `idotmatrix` library (BLE display control)
- `Pillow` (image rendering)
- InfluxDB health bucket access
- Device: iDotMatrix IDM-3B99F5 at `26:C8:1C:3B:99:F5`

## Display Details

- **Resolution:** 16×32 pixels (portrait — 16 wide, 32 tall)
- **Color depth:** RGB (though the display renders somewhat limited)
- **Mode:** DIY raw pixel mode via BLE
