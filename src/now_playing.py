#!/usr/bin/env python3
"""
Now Playing display for the iDotMatrix 64×64 LED panel.

Polls Last.fm for the currently playing track, fetches album art,
resizes to 64×64, dithers for LED display, and pushes via graffiti
pixel mode (the only upload path that works on this device).

When no track is playing (after the hold window expires), cycles
through saved album covers from the idle carousel directory.

Adapted from elwinbb/IDotMatrix-Now-Playing — rewritten for 64×64
and our device's graffiti-mode pixel pushing.

Resilience features:
  - Exponential backoff on Last.fm API errors (5s → 10s → 30s → 60s → 120s)
  - Falls back to carousel mode after 60s of continuous Last.fm errors
  - BLE watchdog: auto-reconnects if connection drops mid-loop (up to 10 attempts)
  - Clean exit for systemd restart only when BLE is truly unreachable

Env vars:
  LASTFM_API_KEY   - Last.fm API key
  LASTFM_USER       - Last.fm username
  POLL_INTERVAL     - seconds between polls (default: 5)
  SHOW_CLOCK        - overlay tiny clock on album art (default: true)
  IDLE_HOLD_SECONDS - how long to show last art after music stops (default: 180)
  IDLE_CAROUSEL_DIR - directory of saved album covers for idle cycling
                      (default: ~/.openclaw/workspace/media)
  IDLE_CAROUSEL_INTERVAL - seconds per cover in idle carousel (default: 15)
"""

import asyncio
import io
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import requests
from PIL import Image, ImageEnhance, ImageDraw
from idotmatrix import ConnectionManager

# --- Monkey-patch: remove 10ms blocking sleep from library's send() ---
# The idotmatrix library calls time.sleep(0.01) after every BLE write.
# With 4096 pixels per image, that's ~41 seconds of dead time per push.
# We override send() to use asyncio.sleep with a much shorter delay (1ms),
# which yields to the event loop without blocking for 10x longer than needed.
import idotmatrix.connectionManager as _idot_cm_mod
import logging as _logging

_original_send = _idot_cm_mod.ConnectionManager.send

async def _patched_send(self, data, response=False):
    if self.client and self.client.is_connected:
        _logging.getLogger("idotmatrix").debug("sending message(s) to device")
        chunk_size = self.client.services.get_characteristic(
            _idot_cm_mod.UUID_WRITE_DATA
        ).max_write_without_response_size
        for i in range(0, len(data), chunk_size):
            await self.client.write_gatt_char(
                _idot_cm_mod.UUID_WRITE_DATA,
                data[i:i + chunk_size],
                response=response,
            )
        # 1ms yield instead of 10ms blocking sleep
        await asyncio.sleep(0.001)
        return True

_idot_cm_mod.ConnectionManager.send = _patched_send
# --- End monkey-patch ---

# Silence library DEBUG log spam (was ~44K lines per 10min in journal)
_logging.getLogger("idotmatrix").setLevel(_logging.WARNING)
_logging.getLogger("bleak").setLevel(_logging.WARNING)

# Load .env from the repo root
DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
if DOTENV_PATH.exists():
    load_dotenv(dotenv_path=DOTENV_PATH, override=False)

# Also check ~/.env (Idoru's secrets file)
HOME_ENV = Path.home() / ".env"
if HOME_ENV.exists():
    load_dotenv(dotenv_path=HOME_ENV, override=False)

# ================== CONFIG ==================
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
LASTFM_USER = os.environ.get("LASTFM_USER", "")

DEVICE_ADDR = "26:C8:1C:3B:99:F5"
DISPLAY_WIDTH = 64
DISPLAY_HEIGHT = 64
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))
IDLE_HOLD_SECONDS = int(os.environ.get("IDLE_HOLD_SECONDS", "180"))
SHOW_CLOCK = os.environ.get("SHOW_CLOCK", "true").lower() in ("1", "true", "yes", "on")

# Backoff config for Last.fm errors
BACKOFF_STEPS = [5, 10, 30, 60, 120]  # seconds between polls on consecutive errors
LASTFM_ERROR_GRACE = 60  # seconds of errors before switching to carousel fallback

# BLE reconnect config
BLE_RECONNECT_DELAY = 5  # seconds between reconnect attempts
BLE_MAX_RECONNECT = 10  # attempts before giving up and letting systemd restart

# Idle carousel config
IDLE_CAROUSEL_DIR = os.environ.get(
    "IDLE_CAROUSEL_DIR",
    str(Path.home() / ".openclaw" / "workspace" / "media"),
)
IDLE_CAROUSEL_INTERVAL = int(os.environ.get("IDLE_CAROUSEL_INTERVAL", "15"))
# Filename patterns that look like album covers (saved by Idoru)
COVER_PATTERNS = ("cover", "album")

LASTFM_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_HEADERS = {"User-Agent": "idoru-now-playing/1.0"}

# Clock overlay config
CLOCK_FG = (255, 255, 255)
CLOCK_BG = (0, 0, 0)

# Tiny pixel font for clock (5×3 glyphs)
GLYPHS = {
    "0": ["###", "# #", "# #", "# #", "###"],
    "1": [" ##", "  #", "  #", "  #", " ###"],
    "2": ["###", "  #", "###", "#  ", "###"],
    "3": ["###", "  #", "###", "  #", "###"],
    "4": ["# #", "# #", "###", "  #", "  #"],
    "5": ["###", "#  ", "###", "  #", "###"],
    "6": ["###", "#  ", "###", "# #", "###"],
    "7": ["###", "  #", "  #", "  #", "  #"],
    "8": ["###", "# #", "###", "# #", "###"],
    "9": ["###", "# #", "###", "  #", "###"],
    ":": ["   ", " # ", "   ", " # ", "   "],
}


def validate_config():
    missing = []
    if not LASTFM_API_KEY:
        missing.append("LASTFM_API_KEY")
    if not LASTFM_USER:
        missing.append("LASTFM_USER")
    if missing:
        raise RuntimeError(f"Missing: {', '.join(missing)}. Set in ~/.env")


def get_now_playing():
    """Returns (image_url, track_name, artist) or None."""
    params = {
        "method": "user.getrecenttracks",
        "user": LASTFM_USER,
        "api_key": LASTFM_API_KEY,
        "format": "json",
        "limit": 1,
    }
    r = requests.get(LASTFM_URL, params=params, headers=LASTFM_HEADERS, timeout=10)
    r.raise_for_status()
    tracks = r.json()["recenttracks"]["track"]
    if not tracks:
        return None

    track = tracks[0]
    if "@attr" not in track or track["@attr"].get("nowplaying") != "true":
        return None

    # Get the largest available image
    image_url = None
    for img in reversed(track.get("image", [])):
        url = (img or {}).get("#text")
        if url:
            image_url = url
            break

    return image_url, track["name"], track["artist"]["#text"]


def fetch_album_art(url):
    """Download album art and return a PIL Image."""
    if not url:
        raise ValueError("No image URL")
    r = requests.get(url, headers=LASTFM_HEADERS, timeout=10)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def load_carousel_covers():
    """Load saved album covers from the carousel directory."""
    covers = []
    carousel_path = Path(IDLE_CAROUSEL_DIR)
    if not carousel_path.is_dir():
        return covers

    for f in sorted(carousel_path.iterdir()):
        if not f.is_file():
            continue
        name = f.name.lower()
        # Only pick up files that look like album covers
        if not any(pat in name for pat in COVER_PATTERNS):
            continue
        if name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            try:
                img = Image.open(f).convert("RGB")
                covers.append((f.name, img))
                print(f"[now-playing] Carousel: loaded {f.name}", file=sys.stderr)
            except Exception as e:
                print(f"[now-playing] Carousel: skip {f.name}: {e}", file=sys.stderr)

    return covers


def directional_dither(img, levels=64):
    """
    Floyd-Steinberg dithering using Pillow's C-optimized quantize().
    Returns an RGB Image with quantized colors — much faster than
    per-pixel Python loops over numpy arrays.
    """
    img = img.convert("RGB")
    # Quantize with Floyd-Steinberg dithering, then convert back to RGB
    # so downstream code (getdata, BLE push) gets the same interface.
    paletted = img.quantize(colors=levels, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    return paletted.convert("RGB")


def overlay_clock(img, now=None):
    """Draw a tiny pixel clock in the bottom-right corner."""
    if not SHOW_CLOCK:
        return img

    now = now or datetime.now()
    text = now.strftime("%I:%M").lstrip("0")
    if not text:
        text = "0:00"

    draw = ImageDraw.Draw(img)

    # Calculate clock dimensions
    char_w = 3
    char_h = 5
    spacing = 1
    total_w = sum(char_w + spacing for _ in text) - spacing
    pad = 1
    margin = 2

    x0 = img.width - total_w - (pad * 2) - margin
    y0 = img.height - char_h - (pad * 2) - margin

    # Background box
    draw.rectangle(
        [x0, y0, x0 + total_w + (pad * 2) - 1, y0 + char_h + (pad * 2) - 1],
        fill=CLOCK_BG,
    )

    # Draw glyphs
    cx = x0 + pad
    cy = y0 + pad
    for ch in text:
        grid = GLYPHS.get(ch)
        if grid:
            for yy, row in enumerate(grid):
                for xx, c in enumerate(row):
                    if c == "#":
                        draw.point((cx + xx, cy + yy), fill=CLOCK_FG)
        cx += char_w + spacing

    return img


def prepare_album_art(url, base_img=None, now=None):
    """
    Download album art, resize to 64×64, enhance, dither, optionally overlay clock.
    Returns a PIL Image (not a file path).
    """
    if base_img is None:
        base_img = fetch_album_art(url)

    img = base_img.copy()

    # Resize to 64×64 — center crop to square first if needed
    w, h = img.size
    if w != h:
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))

    img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.Resampling.BILINEAR)

    # Gentle enhancement
    img = ImageEnhance.Contrast(img).enhance(1.3)
    img = ImageEnhance.Color(img).enhance(1.15)

    # Dither for LED display
    img = directional_dither(img, levels=64)

    # Optional clock overlay
    if SHOW_CLOCK:
        img = overlay_clock(img, now=now)

    return img


async def ble_reconnect(cm):
    """Attempt to reconnect to the BLE display. Returns True on success."""
    for attempt in range(BLE_MAX_RECONNECT):
        try:
            await cm.disconnect()
        except Exception:
            pass
        cm.client = None  # Force fresh BleakClient — library doesn't clear this
        await asyncio.sleep(BLE_RECONNECT_DELAY)
        try:
            print(f"[now-playing] BLE reconnect attempt {attempt+1}/{BLE_MAX_RECONNECT}...", file=sys.stderr)
            await cm.connect()
            await enable_graffiti_mode(cm)
            print("[now-playing] BLE reconnected!", file=sys.stderr)
            return True
        except Exception as e:
            print(f"[now-playing] BLE reconnect failed: {e}", file=sys.stderr)
    return False


async def enable_graffiti_mode(cm):
    """
    Put the display into DIY/graffiti draw mode.
    Without this, the display may be in clock/text mode and pixel writes
    are silently ignored. Must be called after (re)connecting to the display.
    """
    mode_cmd = bytearray([5, 0, 4, 1, 1])  # setMode(1) = enable DIY draw
    try:
        await cm.send(data=mode_cmd)
        print("[now-playing] Graffiti/DIY mode enabled", file=sys.stderr)
    except Exception as e:
        print(f"[now-playing] Failed to set graffiti mode: {e}", file=sys.stderr)


async def push_image_graffiti(cm, img):
    """
    Push a 64×64 PIL Image to the display via graffiti pixel mode.
    Each pixel is sent as an individual BLE write — the iDotMatrix firmware
    only processes one graffiti pixel per write; batching multiple pixels
    in a single send causes all but the first to be silently dropped.
    Returns True on success, False on BLE failure.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.Resampling.BILINEAR)

    pixels = list(img.getdata())
    total = DISPLAY_WIDTH * DISPLAY_HEIGHT

    for i in range(total):
        r, g, b = pixels[i]
        x = i % DISPLAY_WIDTH
        y = i // DISPLAY_WIDTH
        cmd = bytearray([10, 0, 5, 1, 0, r, g, b, x, y])

        try:
            await cm.send(data=cmd)
        except Exception as e:
            print(f"[now-playing] BLE send error at pixel {i}: {e}", file=sys.stderr)
            return False

    return True


async def main_async():
    try:
        validate_config()
    except RuntimeError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    # Load idle carousel covers
    carousel = load_carousel_covers()
    carousel_idx = 0
    carousel_last_push = 0.0
    carousel_current_name = None

    print(f"[now-playing] Connecting to {DEVICE_ADDR}...", file=sys.stderr)
    cm = ConnectionManager()
    cm.address = DEVICE_ADDR

    for attempt in range(5):
        try:
            await cm.connect()
            break
        except Exception as e:
            print(f"[now-playing] Connection attempt {attempt+1}/5 failed: {e}", file=sys.stderr)
            if attempt < 4:
                await asyncio.sleep(3 * (attempt + 1))
            else:
                print("[now-playing] Could not connect to display", file=sys.stderr)
                return 1

    # Enable DIY/graffiti draw mode — without this, pixel writes are ignored
    await enable_graffiti_mode(cm)

    print(f"[now-playing] Connected. Polling Last.fm for {LASTFM_USER}...", file=sys.stderr)

    last_track = None
    last_image_url = None
    last_art_image = None
    last_minute_key = None
    playing_hold_until = None

    # Backoff state for Last.fm errors
    consecutive_lfm_errors = 0
    lfm_error_start = None
    in_carousel_fallback = False

    try:
        while True:
            now = datetime.now()
            minute_key = now.strftime("%Y%m%d%H%M")

            # --- Last.fm poll with backoff ---
            try:
                data = get_now_playing()
                if consecutive_lfm_errors > 0:
                    print(f"[now-playing] Last.fm recovered after {consecutive_lfm_errors} errors", file=sys.stderr)
                consecutive_lfm_errors = 0
                lfm_error_start = None
                in_carousel_fallback = False
            except Exception as e:
                consecutive_lfm_errors += 1
                if lfm_error_start is None:
                    lfm_error_start = time.monotonic()
                error_duration = time.monotonic() - lfm_error_start
                print(f"[now-playing] Last.fm error #{consecutive_lfm_errors}: {e}", file=sys.stderr)

                backoff_idx = min(consecutive_lfm_errors - 1, len(BACKOFF_STEPS) - 1)
                backoff_delay = BACKOFF_STEPS[backoff_idx]

                if error_duration > LASTFM_ERROR_GRACE:
                    if not in_carousel_fallback:
                        print(f"[now-playing] Last.fm down for {error_duration:.0f}s — switching to carousel fallback", file=sys.stderr)
                        in_carousel_fallback = True
                        last_track = None
                        carousel_idx = 0
                        carousel_last_push = 0.0

                if in_carousel_fallback:
                    if carousel and time.monotonic() - carousel_last_push >= IDLE_CAROUSEL_INTERVAL:
                        cover_name, cover_img = carousel[carousel_idx % len(carousel)]
                        img = prepare_album_art("", base_img=cover_img, now=now)
                        push_ok = await push_image_graffiti(cm, img)
                        if not push_ok:
                            if not await ble_reconnect(cm):
                                print("[now-playing] BLE lost during fallback carousel, exiting for systemd restart", file=sys.stderr)
                                return 1
                            retry_ok = await push_image_graffiti(cm, img)
                            if not retry_ok:
                                print("[now-playing] Retry push also failed after reconnect (fallback carousel)", file=sys.stderr)
                        print(f"[now-playing] Fallback carousel: {cover_name} ({carousel_idx % len(carousel) + 1}/{len(carousel)})", file=sys.stderr)
                        carousel_current_name = cover_name
                        carousel_idx += 1
                        carousel_last_push = time.monotonic()
                        last_minute_key = minute_key
                    await asyncio.sleep(POLL_INTERVAL)
                else:
                    await asyncio.sleep(backoff_delay)
                continue

            if data is None:
                # No track playing — hold last art if within hold window
                if playing_hold_until is not None and time.monotonic() < playing_hold_until:
                    if SHOW_CLOCK and last_art_image is not None and minute_key != last_minute_key:
                        img = prepare_album_art(
                            last_image_url or "",
                            base_img=last_art_image,
                            now=now,
                        )
                        push_ok = await push_image_graffiti(cm, img)
                        if not push_ok:
                            if not await ble_reconnect(cm):
                                print("[now-playing] BLE lost during clock update, exiting for systemd restart", file=sys.stderr)
                                return 1
                            retry_ok = await push_image_graffiti(cm, img)
                            if not retry_ok:
                                print("[now-playing] Retry push also failed after reconnect (clock update)", file=sys.stderr)
                        print(f"[now-playing] Clock updated: {now.strftime('%H:%M')}", file=sys.stderr)
                        last_minute_key = minute_key
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                # Genuinely idle — cycle through saved covers
                if last_track is not None:
                    print("[now-playing] Idle — no track playing, starting carousel", file=sys.stderr)
                    last_track = None
                    carousel_idx = 0
                    carousel_last_push = 0.0

                if carousel:
                    if time.monotonic() - carousel_last_push >= IDLE_CAROUSEL_INTERVAL:
                        cover_name, cover_img = carousel[carousel_idx % len(carousel)]
                        img = prepare_album_art("", base_img=cover_img, now=now)
                        push_ok = await push_image_graffiti(cm, img)
                        if not push_ok:
                            if not await ble_reconnect(cm):
                                print("[now-playing] BLE lost during carousel, exiting for systemd restart", file=sys.stderr)
                                return 1
                            retry_ok = await push_image_graffiti(cm, img)
                            if not retry_ok:
                                print("[now-playing] Retry push also failed after reconnect (carousel)", file=sys.stderr)
                        print(f"[now-playing] Carousel: {cover_name} ({carousel_idx % len(carousel) + 1}/{len(carousel)})", file=sys.stderr)
                        carousel_current_name = cover_name
                        carousel_idx += 1
                        carousel_last_push = time.monotonic()
                        last_minute_key = minute_key
                    # Clock overlay on carousel images disabled — re-pushing 4096 pixels
                    # just to update ~20 clock pixels takes ~26s (longer than 1 min),
                    # causing the clock to lag. The carousel rotates often enough that
                    # a clock on transient images isn't worth the extra push.

                await asyncio.sleep(POLL_INTERVAL)
                continue

            image_url, title, artist = data
            track_id = f"{artist} - {title}"
            playing_hold_until = time.monotonic() + IDLE_HOLD_SECONDS

            should_refresh_clock = SHOW_CLOCK and minute_key != last_minute_key
            track_changed = track_id != last_track

            if track_changed or should_refresh_clock:
                if not image_url:
                    print(f"[now-playing] No art for: {track_id}", file=sys.stderr)
                    last_track = track_id
                    last_image_url = image_url
                    last_art_image = None
                    last_minute_key = minute_key
                    continue

                if track_changed or image_url != last_image_url or last_art_image is None:
                    last_art_image = fetch_album_art(image_url)
                    last_image_url = image_url

                img = prepare_album_art(image_url, base_img=last_art_image, now=now)
                push_ok = await push_image_graffiti(cm, img)
                if not push_ok:
                    if not await ble_reconnect(cm):
                        print("[now-playing] BLE lost during art push, exiting for systemd restart", file=sys.stderr)
                        return 1
                    retry_ok = await push_image_graffiti(cm, img)
                    if not retry_ok:
                        print("[now-playing] Retry push also failed after reconnect (art push)", file=sys.stderr)

                if track_changed:
                    print(f"[now-playing] ▶ {artist} - {title}", file=sys.stderr)
                elif should_refresh_clock:
                    print(f"[now-playing] Clock: {now.strftime('%H:%M')}", file=sys.stderr)

                last_track = track_id
                last_minute_key = minute_key

            await asyncio.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[now-playing] Shutting down...", file=sys.stderr)
    finally:
        try:
            await cm.disconnect()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()) or 0)