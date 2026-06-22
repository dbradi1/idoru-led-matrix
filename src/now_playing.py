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
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
import requests
from PIL import Image, ImageEnhance, ImageDraw
import numpy as np
from idotmatrix import ConnectionManager

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
PIXELS_PER_WRITE = 200

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))
IDLE_HOLD_SECONDS = int(os.environ.get("IDLE_HOLD_SECONDS", "180"))
SHOW_CLOCK = os.environ.get("SHOW_CLOCK", "true").lower() in ("1", "true", "yes", "on")

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
    Horizontal error-diffusion dithering to reduce banding on LED matrices.
    Stays in RGB space — no palette conversion.
    """
    img = img.convert("RGB")
    arr = np.array(img, dtype=np.float32)
    h, w, _ = arr.shape
    step = 255.0 / (levels - 1)

    for y in range(h):
        for x in range(w):
            old_pixel = arr[y, x].copy()
            new_pixel = np.round(old_pixel / step) * step
            arr[y, x] = new_pixel
            error = old_pixel - new_pixel
            if x + 1 < w:
                arr[y, x + 1] += error * 0.9
            if y + 1 < h:
                arr[y + 1, x] += error * 0.1

    arr = np.clip(arr, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


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


async def push_image_graffiti(cm, img):
    """
    Push a 64×64 PIL Image to the display via graffiti pixel mode.
    Uses the same batching approach as idoru-led-matrix/src/display.py.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.Resampling.BILINEAR)

    pixels = list(img.getdata())
    total = DISPLAY_WIDTH * DISPLAY_HEIGHT

    for start in range(0, total, PIXELS_PER_WRITE):
        batch = bytearray()
        end = min(start + PIXELS_PER_WRITE, total)

        for i in range(start, end):
            r, g, b = pixels[i]
            x = i % DISPLAY_WIDTH
            y = i // DISPLAY_WIDTH
            batch.extend(bytearray([10, 0, 5, 1, 0, r, g, b, x, y]))

        await cm.send(data=batch)
        await asyncio.sleep(0.05)

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

    print(f"[now-playing] Connected. Polling Last.fm for {LASTFM_USER}...", file=sys.stderr)

    last_track = None
    last_image_url = None
    last_art_image = None
    last_minute_key = None
    playing_hold_until = None

    try:
        while True:
            try:
                now = datetime.now()
                minute_key = now.strftime("%Y%m%d%H%M")

                data = get_now_playing()

                if data is None:
                    # No track playing — hold last art if within hold window
                    if playing_hold_until is not None and time.monotonic() < playing_hold_until:
                        # Update clock if showing
                        if SHOW_CLOCK and last_art_image is not None and minute_key != last_minute_key:
                            img = prepare_album_art(
                                last_image_url or "",
                                base_img=last_art_image,
                                now=now,
                            )
                            await push_image_graffiti(cm, img)
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
                        # Time to show next cover?
                        if time.monotonic() - carousel_last_push >= IDLE_CAROUSEL_INTERVAL:
                            cover_name, cover_img = carousel[carousel_idx % len(carousel)]
                            img = prepare_album_art(
                                "",
                                base_img=cover_img,
                                now=now,
                            )
                            await push_image_graffiti(cm, img)
                            print(f"[now-playing] Carousel: {cover_name} ({carousel_idx % len(carousel) + 1}/{len(carousel)})", file=sys.stderr)
                            carousel_current_name = cover_name
                            carousel_idx += 1
                            carousel_last_push = time.monotonic()
                            last_minute_key = minute_key
                        # Update clock on current carousel cover when minute changes
                        elif SHOW_CLOCK and minute_key != last_minute_key and carousel_current_name:
                            # Find the current cover to re-render with updated clock
                            for name, cover_img in carousel:
                                if name == carousel_current_name:
                                    img = prepare_album_art("", base_img=cover_img, now=now)
                                    await push_image_graffiti(cm, img)
                                    print(f"[now-playing] Carousel clock: {now.strftime('%H:%M')} on {name}", file=sys.stderr)
                                    last_minute_key = minute_key
                                    break

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

                    # Fetch new art if track changed or art URL changed
                    if track_changed or image_url != last_image_url or last_art_image is None:
                        last_art_image = fetch_album_art(image_url)
                        last_image_url = image_url

                    img = prepare_album_art(
                        image_url,
                        base_img=last_art_image,
                        now=now,
                    )
                    await push_image_graffiti(cm, img)

                    if track_changed:
                        print(f"[now-playing] ▶ {artist} - {title}", file=sys.stderr)
                    elif should_refresh_clock:
                        print(f"[now-playing] Clock: {now.strftime('%H:%M')}", file=sys.stderr)

                    last_track = track_id
                    last_minute_key = minute_key

            except Exception as e:
                print(f"[now-playing] Error: {e}", file=sys.stderr)

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