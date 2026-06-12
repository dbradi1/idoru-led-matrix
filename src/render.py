#!/usr/bin/env python3
"""
Pixel-art chart renderer for the iDotMatrix 64×64 LED display.
Generates tiny PNG images optimized for the display's resolution.

Dashboards (64×64 square):
  1. Avg Power (W)        — large numeric + bar + trend indicator
  2. Calories In vs Out   — dual bars with labels + net delta
  3. Net Calorie Balance  — center-zero vertical bar + status
  4. Daily Protein        — progress bar to 130g goal + percentage
  5. Sleep Duration       — stacked bar (deep/rem/core/awake) + quality

At 64×64 we have 4,096 pixels — enough for:
  - 7px header labels with icons
  - Large 10-12px numeric values
  - Rich gradient bars with goal markers
  - Multi-line footer with status + units
  - Decorative borders and dividers
"""

import math
import os
import sys
from typing import Dict, Any, Tuple, List, Optional

from PIL import Image, ImageDraw, ImageFont


# ──────────────────────────────────────────────────
# Canvas: 64×64 square
# ──────────────────────────────────────────────────

WIDTH = 64
HEIGHT = 64

# Color palette — cyberpunk Idoru theme
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
DARKER   = (15, 10, 30)     # Darker fill
PINK     = (255, 80, 180)   # Accent pink

# Font setup
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def get_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


FONT_HDR = None   # 7px for headers
FONT_VAL = None   # 10px for big values
FONT_SM  = None   # 6px for small labels
FONT_XS  = None   # 5px for tiny annotations


def _init_fonts():
    global FONT_HDR, FONT_VAL, FONT_SM, FONT_XS
    if FONT_HDR is None:
        FONT_HDR = get_font(7)
        FONT_VAL = get_font(10)
        FONT_SM  = get_font(6)
        FONT_XS  = get_font(5)


def new_canvas() -> Image.Image:
    """Create a fresh 64×64 canvas with dark background."""
    return Image.new("RGB", (WIDTH, HEIGHT), BG)


def draw_hline(draw, y, color=DIM, x0=1, x1=None):
    """Horizontal rule."""
    if x1 is None:
        x1 = WIDTH - 1
    for x in range(x0, x1):
        draw.point((x, y), color)


def draw_vline(draw, x, color=DIM, y0=1, y1=None):
    """Vertical rule."""
    if y1 is None:
        y1 = HEIGHT - 1
    for y in range(y0, y1):
        draw.point((x, y), color)


def draw_rect(draw, x, y, w, h, color, fill=False):
    """Draw a rectangle outline or filled."""
    if fill:
        for dy in range(h):
            for dx in range(w):
                px, py = x + dx, y + dy
                if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                    draw.point((px, py), color)
    else:
        draw_hline(draw, y, color, x, x + w)
        draw_hline(draw, y + h - 1, color, x, x + w)
        draw_vline(draw, x, color, y, y + h)
        draw_vline(draw, x + w - 1, color, y, y + h)


def draw_text(draw, text: str, y: int, color, font_size: int = 7, 
              center: bool = True, x_offset: int = 0):
    """Draw text with dark outline for contrast."""
    _init_fonts()
    if font_size <= 5:
        font = FONT_XS
    elif font_size <= 6:
        font = FONT_SM
    elif font_size <= 7:
        font = FONT_HDR
    else:
        font = FONT_VAL
    
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    
    if center:
        x = (WIDTH - tw) // 2 + x_offset
    else:
        x = 2 + x_offset
    x = max(1, min(x, WIDTH - tw - 1))
    
    # Dark outline
    for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)]:
        draw.text((x + ox, y + oy), text, fill=BG, font=font)
    draw.text((x, y), text, fill=color, font=font)


def draw_text_right(draw, text: str, y: int, color, font_size: int = 6):
    """Right-aligned text."""
    _init_fonts()
    font = FONT_SM if font_size <= 6 else FONT_HDR
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = WIDTH - tw - 2
    x = max(1, x)
    for ox, oy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        draw.text((x + ox, y + oy), text, fill=BG, font=font)
    draw.text((x, y), text, fill=color, font=font)


# ──────────────────────────────────────────────────
# Bar rendering
# ──────────────────────────────────────────────────

def draw_hbar(draw, x: int, y: int, w: int, h: int, ratio: float, 
              color: Tuple[int, int, int], bg_color=None, 
              gradient: bool = True):
    """Draw a horizontal progress bar with optional gradient."""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(w * ratio)
    
    if bg_color is None:
        bg_color = DARKER
    
    for dy in range(h):
        for dx in range(w):
            px, py = x + dx, y + dy
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                if dx < filled:
                    if gradient:
                        # Brighter at top, dimmer at bottom
                        brightness = 0.5 + 0.5 * (1.0 - dy / max(h, 1))
                        # Also brighter at left edge
                        edge_bright = 0.85 + 0.15 * (1.0 - dx / max(filled, 1))
                        brightness = brightness * 0.7 + edge_bright * 0.3
                        c = tuple(min(255, int(ch * brightness)) for ch in color)
                    else:
                        c = color
                    draw.point((px, py), c)
                else:
                    draw.point((px, py), bg_color)


def draw_vbar(draw, x: int, y: int, w: int, h: int, ratio: float,
              color, upward: bool = True, bg_color=None, gradient: bool = True):
    """Draw a vertical progress bar."""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(h * ratio)
    
    if bg_color is None:
        bg_color = DARKER
    
    for dy in range(h):
        px_row = y + (h - 1 - dy) if upward else y + dy
        for dx in range(w):
            px, py = x + dx, px_row
            if 0 <= px < WIDTH and 0 <= py < HEIGHT:
                if dy < filled:
                    if gradient:
                        brightness = 0.5 + 0.5 * (1.0 - dx / max(w, 1))
                        c = tuple(min(255, int(ch * brightness)) for ch in color)
                    else:
                        c = color
                    draw.point((px, py), c)
                else:
                    draw.point((px, py), bg_color)


def draw_dual_hbar(draw, x, y, w, h, ratio1, color1, ratio2, color2, 
                   gap: int = 1, bg_color=None):
    """Two bars side by side in the same row (for comparison)."""
    if bg_color is None:
        bg_color = DARKER
    
    half_w = (w - gap) // 2
    draw_hbar(draw, x, y, half_w, h, ratio1, color1, bg_color)
    draw_hbar(draw, x + half_w + gap, y, half_w, h, ratio2, color2, bg_color)


# ──────────────────────────────────────────────────
# Dashboard renderers
# ──────────────────────────────────────────────────

def render_avg_power(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 1: Avg Power (watts) — large value + bar + status."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    watts = data.get("avg_power_watts", 0)
    
    # ── Header bar ──
    draw_rect(draw, 0, 0, WIDTH, 8, PURPLE, fill=True)
    draw_text(draw, "⚡ POWER", 1, WHITE, 7)
    
    # ── Big value ──
    val_str = f"{watts:.0f}"
    draw_text(draw, val_str, 12, WHITE, 10)
    draw_text(draw, "WATTS", 22, CYAN, 6)
    
    # ── Bar (14px tall, full width) ──
    max_w = 300
    ratio = min(watts / max_w, 1.0) if max_w > 0 else 0
    
    if ratio < 0.25:
        bar_color = CYAN
    elif ratio < 0.5:
        bar_color = GREEN
    elif ratio < 0.75:
        bar_color = YELLOW
    else:
        bar_color = RED
    
    bar_y = 30
    bar_h = 14
    bar_w = WIDTH - 4
    draw_hbar(draw, 2, bar_y, bar_w, bar_h, ratio, bar_color)
    
    # Tick marks on bar
    for tick_pct in [0.25, 0.5, 0.75]:
        tx = 2 + int(bar_w * tick_pct)
        for dy in range(bar_h):
            if dy % 3 == 0:
                draw.point((tx, bar_y + dy), BG)
    
    # ── Footer ──
    draw_hline(draw, 48, DIM)
    
    if watts < 50:
        label, lc = "REST", DIM
    elif watts < 100:
        label, lc = "EASY", CYAN
    elif watts < 175:
        label, lc = "MOD", GREEN
    elif watts < 250:
        label, lc = "HARD", YELLOW
    else:
        label, lc = "MAX", RED
    
    draw_text(draw, label, 50, lc, 7)
    draw_text(draw, f"{ratio*100:.0f}%", 57, DIM, 5)
    
    return img


def render_calories_in_out(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 2: Calories In vs Out — dual bars + net delta."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    cals_in = data.get("calories_in", 0)
    cals_out = data.get("calories_out", 0)
    
    # ── Header ──
    draw_rect(draw, 0, 0, WIDTH, 8, PURPLE, fill=True)
    draw_text(draw, "🔥 KCAL", 1, WHITE, 7)
    
    # ── Scale ──
    max_cal = max(cals_in, cals_out, 100)
    max_cal = math.ceil(max_cal / 500) * 500
    
    # ── IN bar (green, top) ──
    in_ratio = cals_in / max_cal if max_cal > 0 else 0
    bar_w = WIDTH - 4
    bar_h = 10
    
    draw_text(draw, "IN", 10, GREEN, 6, center=False)
    draw_text_right(draw, f"{cals_in:.0f}", 10, GREEN, 6)
    draw_hbar(draw, 2, 18, bar_w, bar_h, in_ratio, GREEN)
    
    # ── OUT bar (orange, bottom) ──
    out_ratio = cals_out / max_cal if max_cal > 0 else 0
    
    draw_text(draw, "OUT", 30, ORANGE, 6, center=False)
    draw_text_right(draw, f"{cals_out:.0f}", 30, ORANGE, 6)
    draw_hbar(draw, 2, 38, bar_w, bar_h, out_ratio, ORANGE)
    
    # ── Net delta ──
    diff = cals_in - cals_out
    draw_hline(draw, 50, DIM)
    
    if diff > 0:
        delta_str = f"+{diff:.0f} kcal"
        dc = GREEN
        status = "SURPLUS"
    elif diff < 0:
        delta_str = f"{diff:.0f} kcal"
        dc = RED if diff < -500 else YELLOW
        status = "DEFICIT"
    else:
        delta_str = "0 kcal"
        dc = WHITE
        status = "BALANCED"
    
    draw_text(draw, delta_str, 52, dc, 7)
    draw_text(draw, status, 59, DIM, 5)
    
    return img


def render_net_calories(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 3: Net Calorie Balance — center-zero vertical bar."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    net = data.get("net_calories", 0)
    
    # ── Header ──
    draw_rect(draw, 0, 0, WIDTH, 8, PURPLE, fill=True)
    draw_text(draw, "⚖ NET", 1, WHITE, 7)
    
    # ── Center zero line ──
    center_y = 32
    draw_hline(draw, center_y, DIM, 4, WIDTH - 4)
    
    # ── Value ──
    sign = "+" if net >= 0 else "-"
    val_str = f"{sign}{abs(net):.0f}"
    draw_text(draw, val_str, 10, WHITE, 10)
    draw_text(draw, "kcal", 20, CYAN, 6)
    
    # ── Vertical bar from center ──
    max_net = 2000
    bar_h_max = 20  # pixels above/below center
    ratio = min(abs(net) / max_net, 1.0) if max_net > 0 else 0
    bar_w = 20
    
    bar_x = (WIDTH - bar_w) // 2
    
    if net > 0:
        # Surplus: bar going UP from center
        if net < 500:
            color = GREEN
        elif net < 1000:
            color = YELLOW
        else:
            color = RED
        bar_h = int(bar_h_max * ratio)
        if bar_h > 0:
            draw_vbar(draw, bar_x, center_y - bar_h, bar_w, bar_h, 1.0, color, upward=True)
    elif net < 0:
        # Deficit: bar going DOWN from center
        if abs(net) < 500:
            color = CYAN
        else:
            color = BLUE
        bar_h = int(bar_h_max * ratio)
        if bar_h > 0:
            draw_vbar(draw, bar_x, center_y, bar_w, bar_h, 1.0, color, upward=False)
    
    # ── Footer ──
    draw_hline(draw, 55, DIM)
    
    if net > 500:
        status, sc = "SURPLUS ↑", YELLOW
    elif net > 0:
        status, sc = "slight +", GREEN
    elif net < -500:
        status, sc = "DEFICIT ↓", BLUE
    elif net < 0:
        status, sc = "slight -", CYAN
    else:
        status, sc = "BALANCED", WHITE
    
    draw_text(draw, status, 57, sc, 7)
    
    return img


def render_protein(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 4: Daily Protein — progress bar to 130g goal."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    protein = data.get("protein_grams", 0)
    goal = 130
    
    # ── Header ──
    draw_rect(draw, 0, 0, WIDTH, 8, PURPLE, fill=True)
    draw_text(draw, "🥩 PROTEIN", 1, WHITE, 7)
    
    # ── Big value ──
    draw_text(draw, f"{protein:.0f}", 12, WHITE, 10)
    draw_text(draw, "grams", 22, CYAN, 6)
    
    # ── Progress bar (16px tall) ──
    ratio = min(protein / goal, 1.0) if goal > 0 else 0
    
    if ratio < 0.33:
        bar_color = RED
    elif ratio < 0.5:
        bar_color = ORANGE
    elif ratio < 0.75:
        bar_color = YELLOW
    else:
        bar_color = GREEN
    
    bar_y = 30
    bar_h = 16
    bar_w = WIDTH - 4
    draw_hbar(draw, 2, bar_y, bar_w, bar_h, ratio, bar_color)
    
    # Goal marker line
    goal_x = 2 + bar_w - 1
    for dy in range(bar_h):
        if dy % 2 == 0:
            draw.point((goal_x, bar_y + dy), WHITE)
    
    # Goal label
    draw_text_right(draw, f"/{goal}g", bar_y + 2, WHITE, 6)
    
    # ── Percentage + status ──
    pct = int(ratio * 100)
    draw_hline(draw, 50, DIM)
    
    if ratio >= 1.0:
        status, sc = "HIT GOAL!", GREEN
    elif ratio >= 0.75:
        status, sc = "CLOSE", YELLOW
    elif ratio >= 0.5:
        status, sc = "HALFWAY", ORANGE
    elif ratio > 0:
        status, sc = "LOW", RED
    else:
        status, sc = "NONE", DIM
    
    draw_text(draw, f"{pct}%", 52, bar_color, 7)
    draw_text(draw, status, 58, sc, 5)
    
    return img


def render_sleep(data: Dict[str, Any]) -> Image.Image:
    """Dashboard 5: Sleep Duration — stacked bar + quality."""
    img = new_canvas()
    draw = ImageDraw.Draw(img)
    
    sleep = data.get("sleep_detail", {})
    total = sleep.get("total", 0)
    deep = sleep.get("deep", 0)
    rem = sleep.get("rem", 0)
    core = sleep.get("core", 0)
    awake = sleep.get("awake", 0)
    
    # ── Header ──
    draw_rect(draw, 0, 0, WIDTH, 8, PURPLE, fill=True)
    draw_text(draw, "😴 SLEEP", 1, WHITE, 7)
    
    # ── Big value ──
    h = int(total)
    m = int((total - h) * 60)
    draw_text(draw, f"{h}h {m:02d}m", 12, WHITE, 10)
    
    # ── Stacked sleep bar (18px tall) ──
    max_sleep = 10.0
    bar_y = 24
    bar_h = 18
    bar_w = WIDTH - 4
    
    segments = [
        (deep,  PURPLE, "DEEP"),
        (rem,   CYAN,   "REM"),
        (core,  BLUE,   "CORE"),
        (awake, (60, 15, 25), "AWK"),
    ]
    
    x_cursor = 2
    for seg_hours, seg_color, seg_label in segments:
        seg_ratio = min(seg_hours / max_sleep, 1.0) if max_sleep > 0 else 0
        seg_w = max(0, int(bar_w * seg_ratio))
        if seg_w > 0 and x_cursor < WIDTH - 2:
            actual_w = min(seg_w, WIDTH - 2 - x_cursor)
            draw_hbar(draw, x_cursor, bar_y, actual_w, bar_h, 1.0, seg_color, seg_color, gradient=False)
            x_cursor += actual_w
    
    # Fill remaining
    if x_cursor < WIDTH - 2:
        draw_rect(draw, x_cursor, bar_y, WIDTH - 2 - x_cursor, bar_h, DARKER, fill=True)
    
    # ── Legend ──
    legend_y = 44
    legend_items = [
        (2, PURPLE, "D"),
        (18, CYAN, "R"),
        (34, BLUE, "C"),
        (50, (60, 15, 25), "A"),
    ]
    for lx, lc, ll in legend_items:
        draw_text(draw, ll, legend_y, lc, 6, center=True, x_offset=lx - WIDTH//2)
    
    # ── Quality footer ──
    draw_hline(draw, 52, DIM)
    
    if total >= 8:
        q, qc = "OPTIMAL", GREEN
    elif total >= 7:
        q, qc = "GOOD", CYAN
    elif total >= 6:
        q, qc = "OK", YELLOW
    elif total > 0:
        q, qc = "LOW", RED
    else:
        q, qc = "NO DATA", DIM
    
    draw_text(draw, q, 55, qc, 7)
    
    # Deep sleep % if we have data
    if total > 0:
        deep_pct = int(deep / total * 100)
        draw_text(draw, f"deep {deep_pct}%", 61, PURPLE, 5)
    
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
