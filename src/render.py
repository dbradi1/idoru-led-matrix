#!/usr/bin/env python3
"""
Pixel-art chart renderer for the iDotMatrix 16×32 LED display.
Generates tiny PNG images optimized for the display's resolution.

Dashboards (16×32 portrait — 16 wide, 32 tall):
  1. Avg Power (W)        — horizontal bar with watt value
  2. Calories In vs Out   — dual bars with kcal labels
  3. Net Calorie Balance  — surplus/deficit bar from center
  4. Daily Protein        — progress bar toward 130g goal
  5. Sleep Duration       — stacked bar (deep/rem/core/awake)

At 16×32, readable text is extremely limited. Each dashboard uses:
  - 3px top header label
  - Main visual bar/chart (fills middle ~20px)
  - 4px bottom footer with value
"""

import math
import os
import sys
from typing import Dict, Any, Tuple, List, Optional

from PIL import Image, ImageDraw, ImageFont


# ──────────────────────────────────────────────────
# Canvas: 16×32 portrait
# ──────────────────────────────────────────────────

WIDTH = 16
HEIGHT = 32

# Color palette
BG       = (4,  2,  12)     # Near-black purple
PURPLE   = (140, 30, 230)   # Idoru purple
CYAN     = (0,  230, 230)   # Cyan
GREEN    = (40, 255, 80)    # Neon green
YELLOW   = (240, 210, 30)   # Yellow
ORANGE   = (240, 130, 20)   # Orange
RED      = (240, 30, 30)    # Red
WHITE    = (220, 210, 245)  # Soft white
BLUE     = (50, 130, 255)   # Blue
DIM      = (50, 35, 80)     # Dim accent

# Font setup — use the smallest readable size
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def get_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


FONT_HDR = None  # 5px for headers
FONT_VAL = None  # 6px for values


def _init_fonts():
    global FONT_HDR, FONT_VAL
    if FONT_HDR is None:
        FONT_HDR = get_font(5)
        FONT_VAL = get_font(6)


def new_canvas() -> Image.Image:
    """Create a fresh 16×32 canvas with dark background."""
    return Image.new("RGB", (WIDTH, HEIGHT), BG)


def draw_hline(draw, y, color=DIM):
    """Horizontal rule at y with padding."""
    for x in range(1, WIDTH - 1):
        draw.point((x, y), color)


def draw_text(draw, text: str, y: int, color, font_size: int = 5, center: bool = True):
    """Draw tiny centered text using Pillow."""
    _init_fonts()
    font = FONT_HDR if font_size <= 5 else FONT_VAL
    
    # Use ImageDraw text with tiny font
    # Get text width via textbbox (Pillow 8+)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    
    x = (WIDTH - tw) // 2 if center else 1
    x = max(0, min(x, WIDTH - tw))
    
    # Draw with a dark outline for contrast
    for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        draw.text((x + ox, y + oy + 1), text, fill=BG, font=font)
    draw.text((x, y + 1), text, fill=color, font=font)


# ──────────────────────────────────────────────────
# Bar rendering
# ──────────────────────────────────────────────────

def draw_hbar(draw, x: int, y: int, w: int, h: int, ratio: float, 
              color: Tuple[int, int, int], bg_color=None):
    """Draw a horizontal progress bar.
    
    ratio: 0.0 = empty, 1.0 = full
    """
    ratio = max(0.0, min(1.0, ratio))
    filled = int(w * ratio)
    
    if bg_color is None:
        bg_color = DIM
    
    for dy in range(h):
        for dx in range(w):
            px, py = x + dx, y + dy
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                if dx < filled:
                    # Gradient: brighter near top of bar
                    brightness = 0.6 + 0.4 * (1.0 - dy / max(h, 1))
                    c = tuple(min(255, int(ch * brightness)) for ch in color)
                    draw.point((px, py), c)
                else:
                    draw.point((px, py), bg_color)


def draw_vbar(draw, x: int, y: int, w: int, h: int, ratio: float,
              color, upward: bool = True, bg_color=None):
    """Draw a vertical progress bar (fills from bottom or top)."""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(h * ratio)
    
    if bg_color is None:
        bg_color = DIM
    
    for dy in range(h):
        px_row = y + (h - 1 - dy) if upward else y + dy
        for dx in range(w):
            px, py = x + dx, px_row
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                if dy < filled:
                    brightness = 0.6 + 0.4 * (1.0 - dx / max(w, 1))
                    c = tuple(min(255, int(ch * brightness)) for ch in color)
                    draw.point((px, py), c)
                else:
                    draw.point((px, py), bg_color)


# ──────────────────────────────────────────────────
# Dashboard renderers
# ──────────────────────────────────────────────────

def render_avg_power(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 1: Avg Power (watts) — bar + numeric value."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    watts = data.get("avg_power_watts", 0)
    
    # Header
    draw_hline(draw, 0, PURPLE)
    draw_text(draw, "WATT", 0, CYAN, 5)
    draw_hline(draw, 4, DIM)
    
    # Big value
    val_str = f"{watts:.0f}"
    draw_text(draw, val_str, 6, WHITE, 6)
    
    # Bar (8px tall)
    max_w = 300
    ratio = min(watts / max_w, 1.0) if max_w > 0 else 0
    
    if ratio < 0.33:
        bar_color = CYAN
    elif ratio < 0.66:
        bar_color = YELLOW
    else:
        bar_color = ORANGE
    
    draw_hbar(draw, 1, 14, WIDTH - 2, 6, ratio, bar_color)
    
    # Unit indicator
    draw_text(draw, "W", 21, PURPLE, 5)
    
    # Footer with activity level
    if watts < 50:
        label = "LOW"
        lc = DIM
    elif watts < 150:
        label = "MOD"
        lc = GREEN
    elif watts < 250:
        label = "HI"
        lc = YELLOW
    else:
        label = "MAX"
        lc = RED
    
    draw_hline(draw, 27, DIM)
    draw_text(draw, label, 27, lc, 5)
    
    return img


def render_calories_in_out(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 2: Calories In vs Out — dual horizontal bars."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    cals_in = data.get("calories_in", 0)
    cals_out = data.get("calories_out", 0)
    
    # Header
    draw_hline(draw, 0, PURPLE)
    draw_text(draw, "KCAL", 0, CYAN, 5)
    draw_hline(draw, 4, DIM)
    
    # Scale: round up to nearest 500
    max_cal = max(cals_in, cals_out, 100)
    max_cal = math.ceil(max_cal / 500) * 500
    
    # IN bar (green)
    in_ratio = cals_in / max_cal if max_cal > 0 else 0
    draw_hbar(draw, 1, 6, WIDTH - 2, 5, in_ratio, GREEN)
    
    # IN label
    draw_text(draw, f"IN{cals_in:.0f}", 6, GREEN, 5, center=False)
    
    # OUT bar (orange) with gap
    out_ratio = cals_out / max_cal if max_cal > 0 else 0
    draw_hbar(draw, 1, 13, WIDTH - 2, 5, out_ratio, ORANGE)
    
    # OUT label
    draw_text(draw, f"OUT{cals_out:.0f}", 13, ORANGE, 5, center=False)
    
    # Comparison footer
    diff = cals_in - cals_out
    if diff > 0:
        footer = f"+{diff:.0f}"
        fc = GREEN
    else:
        footer = f"{diff:.0f}"
        fc = RED if diff < -500 else YELLOW
    
    draw_hline(draw, 27, DIM)
    draw_text(draw, footer, 27, fc, 5)
    
    return img


def render_net_calories(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 3: Net Calorie Balance — center-zero bar."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    net = data.get("net_calories", 0)
    
    # Header
    draw_hline(draw, 0, PURPLE)
    draw_text(draw, "NET", 0, CYAN, 5)
    draw_hline(draw, 4, DIM)
    
    # Center line at pixel row 17
    center_y = 17
    for x in range(2, WIDTH - 2):
        draw.point((x, center_y), DIM)
    
    # Value
    sign = "+" if net >= 0 else "-"
    val_str = f"{sign}{abs(net):.0f}"
    draw_text(draw, val_str, 5, WHITE, 6)
    
    # Bar from center (vertical bar, 12px max height)
    max_net = 1500
    bar_h_max = 12
    ratio = min(abs(net) / max_net, 1.0) if max_net > 0 else 0
    bar_w = 8
    
    if net > 0:
        # Surplus: green/orange bar going UP from center
        color = GREEN if net < 500 else YELLOW if net < 1000 else ORANGE
        draw_vbar(draw, (WIDTH - bar_w) // 2, center_y - int(bar_h_max * ratio),
                  bar_w, int(bar_h_max * ratio), 1.0, color, upward=True)
    elif net < 0:
        # Deficit: cyan bar going DOWN from center
        color = CYAN if abs(net) < 500 else BLUE
        draw_vbar(draw, (WIDTH - bar_w) // 2, center_y,
                  bar_w, int(bar_h_max * ratio), 1.0, color, upward=False)
    
    # Footer status
    if net > 0:
        status = "SUR+" if net > 500 else "ok+"
        sc = GREEN if net <= 500 else YELLOW
    elif net < 0:
        status = "DEF" if abs(net) > 500 else "ok-"
        sc = CYAN if abs(net) <= 500 else BLUE
    else:
        status = "BAL"
        sc = WHITE
    
    draw_hline(draw, 27, DIM)
    draw_text(draw, status, 27, sc, 5)
    
    return img


def render_protein(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 4: Daily Protein — progress bar to 130g goal."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    protein = data.get("protein_grams", 0)
    goal = 130
    
    # Header
    draw_hline(draw, 0, PURPLE)
    draw_text(draw, "PROT", 0, CYAN, 5)
    draw_hline(draw, 4, DIM)
    
    # Value
    draw_text(draw, f"{protein:.0f}g", 5, WHITE, 6)
    
    # Progress bar (10px tall)
    ratio = min(protein / goal, 1.0) if goal > 0 else 0
    
    if ratio < 0.33:
        bar_color = RED
    elif ratio < 0.66:
        bar_color = ORANGE
    elif ratio < 0.85:
        bar_color = YELLOW
    else:
        bar_color = GREEN
    
    bar_y = 12
    bar_h = 10
    draw_hbar(draw, 1, bar_y, WIDTH - 2, bar_h, ratio, bar_color)
    
    # Goal marker at right edge
    for dy in range(bar_h):
        draw.point((WIDTH - 2, bar_y + dy), WHITE)
    
    # Percentage
    pct = int(ratio * 100)
    draw_hline(draw, 27, DIM)
    draw_text(draw, f"{pct}%", 27, bar_color, 5)
    
    return img


def render_sleep(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 5: Sleep Duration — stacked segmented bar."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    sleep = data.get("sleep_detail", {})
    total = sleep.get("total", 0)
    deep = sleep.get("deep", 0)
    rem = sleep.get("rem", 0)
    core = sleep.get("core", 0)
    awake = sleep.get("awake", 0)
    
    # Header
    draw_hline(draw, 0, PURPLE)
    draw_text(draw, "SLEP", 0, CYAN, 5)
    draw_hline(draw, 4, DIM)
    
    # Value
    h = int(total)
    m = int((total - h) * 60)
    draw_text(draw, f"{h}h{m:02d}", 5, WHITE, 6)
    
    # Stacked sleep bar (10px tall)
    max_sleep = 10.0
    bar_y = 12
    bar_h = 10
    
    segments = [
        (deep, PURPLE),
        (rem, CYAN),
        (core, BLUE),
        (awake, (60, 15, 25)),  # Dark red for awake
    ]
    
    x_cursor = 1
    for seg_hours, seg_color in segments:
        seg_ratio = min(seg_hours / max_sleep, 1.0) if max_sleep > 0 else 0
        seg_w = max(0, int((WIDTH - 2) * seg_ratio))
        if seg_w > 0 and x_cursor < WIDTH - 1:
            actual_w = min(seg_w, WIDTH - 1 - x_cursor)
            draw_hbar(draw, x_cursor, bar_y, actual_w, bar_h, 1.0, seg_color, seg_color)
            x_cursor += actual_w
    
    # Fill remaining with BG if total < max
    if x_cursor < WIDTH - 1:
        for dy in range(bar_h):
            for dx in range(x_cursor, WIDTH - 1):
                draw.point((dx, bar_y + dy), (15, 10, 30))
    
    # Quality footer
    if total >= 7.5:
        q = "OPT"
        qc = GREEN
    elif total >= 6:
        q = " OK"
        qc = YELLOW
    elif total > 0:
        q = "LOW"
        qc = RED
    else:
        q = "N/A"
        qc = DIM
    
    draw_hline(draw, 27, DIM)
    draw_text(draw, q, 27, qc, 5)
    
    return img


# ──────────────────────────────────────────────────
# Render all
# ──────────────────────────────────────────────────

RENDERERS = {
    "1_power":       ("Avg Power", render_avg_power),
    "2_calories":    ("Calories In vs Out", render_calories_in_out),
    "3_net":         ("Net Calorie Balance", render_net_calories),
    "4_protein":     ("Daily Protein", render_protein),
    "5_sleep":       ("Sleep Duration", render_sleep),
}


def render_all(data: Dict[str, Any], output_dir: str = ".") -> List[str]:
    """Render all 5 dashboards and save as PNGs. Returns list of file paths."""
    paths = []
    for key, (name, renderer) in RENDERERS.items():
        img = renderer(data)
        path = os.path.join(output_dir, f"dashboard_{key}.png")
        img.save(path, "PNG")
        paths.append(path)
        print(f"[render] {name} → {path} ({WIDTH}×{HEIGHT})", file=sys.stderr)
    return paths


def render_single(data: Dict[str, Any], key: str, output_dir: str = ".") -> str:
    """Render a single dashboard by key. Returns file path."""
    name, renderer = RENDERERS[key]
    img = renderer(data)
    path = os.path.join(output_dir, f"dashboard_{key}.png")
    img.save(path, "PNG")
    print(f"[render] {name} → {path} ({WIDTH}×{HEIGHT})", file=sys.stderr)
    return path


if __name__ == "__main__":
    import json
    
    output_dir = "."
    if len(sys.argv) > 1:
        if sys.argv[1] == "--output-dir" and len(sys.argv) > 2:
            output_dir = sys.argv[2]
        elif sys.argv[1] in RENDERERS:
            # Render single dashboard
            test_data = {
                "avg_power_watts": 175,
                "calories_in": 2100,
                "calories_out": 2580,
                "net_calories": -480,
                "protein_grams": 95,
                "sleep_hours": 7.2,
                "sleep_detail": {"total": 7.2, "deep": 1.8, "rem": 2.1, "core": 2.8, "awake": 0.5},
            }
            os.makedirs(output_dir, exist_ok=True)
            render_single(test_data, sys.argv[1], output_dir)
            sys.exit(0)
    
    os.makedirs(output_dir, exist_ok=True)
    
    test_data = {
        "avg_power_watts": 175,
        "calories_in": 2100,
        "calories_out": 2580,
        "net_calories": -480,
        "protein_grams": 95,
        "sleep_hours": 7.2,
        "sleep_detail": {"total": 7.2, "deep": 1.8, "rem": 2.1, "core": 2.8, "awake": 0.5},
    }
    
    paths = render_all(test_data, output_dir)
    print(f"\nRendered {len(paths)} dashboards to {output_dir}/")
