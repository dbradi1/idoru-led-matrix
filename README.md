# Idoru Now Playing Display 🎴

Last.fm now-playing display for the iDotMatrix 64×64 BLE LED panel. Polls Last.fm for the currently playing track, fetches album art, resizes to 64×64, dithers for LED display, and pushes via graffiti pixel mode.

When no track is playing (after a 3-minute hold window), cycles through saved album covers from the idle carousel directory.

## Features

- **Now Playing** — Shows album art for the current Last.fm track
- **Idle Carousel** — Cycles through saved album covers when no music is playing
- **Clock Overlay** — Optional tiny pixel clock overlaid on album art (when music is playing)
- **Resilience** — Exponential backoff on API errors, BLE auto-reconnect (up to 10 attempts), carousel fallback when Last.fm is down, external watchdog timer

## Architecture

```
Last.fm API → fetch album art → resize/dither to 64×64 → BLE push (graffiti pixel mode)
                                    ↑
                        Idle carousel (when no track playing)
```

## Files

- `src/now_playing.py` — Main service (poll, render, push, carousel, resilience)
- `scripts/idoru-led-matrix.service` — systemd user service file
- `scripts/idoru-display-watchdog.sh` — External watchdog (restarts on BLE failures)

## Setup

```bash
# Create venv
python3 -m venv ~/.idoru-display-env
source ~/.idoru-display-env/bin/activate
pip install -r requirements.txt

# Configure env vars (in ~/.env or repo .env)
LASTFM_API_KEY=your_key
LASTFM_USER=your_username

# Run
python3 src/now_playing.py
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LASTFM_API_KEY` | (required) | Last.fm API key |
| `LASTFM_USER` | (required) | Last.fm username |
| `POLL_INTERVAL` | `5` | Seconds between Last.fm polls |
| `SHOW_CLOCK` | `true` | Overlay clock on album art |
| `IDLE_HOLD_SECONDS` | `180` | How long to show last art after music stops |
| `IDLE_CAROUSEL_DIR` | `~/.openclaw/workspace/media` | Directory of saved album covers |
| `IDLE_CAROUSEL_INTERVAL` | `15` | Seconds per cover in idle carousel |

## Display Details

- **Device:** iDotMatrix IDM-3B99F5
- **BLE Address:** `26:C8:1C:3B:99:F5`
- **Resolution:** 64×64 pixels
- **Mode:** DIY/graffiti raw pixel mode via BLE
- **Push:** One pixel per BLE write (firmware limitation)

## systemd Service

```ini
[Unit]
Description=Idoru Now Playing Display (LED Matrix - Last.fm)
After=network-online.target bluetooth.target
Wants=network-online.target bluetooth.target
Requires=bluetooth.service

[Service]
Type=simple
ExecStart=/home/drew/.idoru-display-env/bin/python3 /home/drew/github-repos/idoru-led-matrix/src/now_playing.py
Restart=always
RestartSec=30
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/drew/.env

[Install]
WantedBy=default.target
```