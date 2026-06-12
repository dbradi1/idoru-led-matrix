#!/usr/bin/env python3
"""
Main cycle: fetch health data → render pixel dashboards → push to LED matrix.

Run modes:
  --once      Run one cycle and exit
  --loop      Run continuously (default: 60s between cycles)
  --pause N   Seconds between cycles (default: 60)
  --render-only   Generate PNGs but don't push to display
"""

import asyncio
import os
import sys
from datetime import datetime

# Ensure we can import sibling modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch import fetch_all
from render import render_all, RENDERERS
from display import push_sequence

OUTPUT_DIR = "/home/drew/.cache/idoru-led-matrix"


async def run_cycle(push_to_display: bool = True, display_time: float = 3.0):
    """Run one full cycle: fetch → render → push."""
    start = datetime.now()
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"[cycle] Starting at {start.strftime('%H:%M:%S')}", file=sys.stderr)
    
    # 1. Fetch health data
    print("[cycle] Fetching health data...", file=sys.stderr)
    data = fetch_all()
    
    # 2. Render dashboards
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("[cycle] Rendering dashboards...", file=sys.stderr)
    paths = render_all(data, OUTPUT_DIR)
    
    # 3. Push to display
    if push_to_display:
        print("[cycle] Pushing to LED matrix...", file=sys.stderr)
        success = await push_sequence(paths, display_time=display_time)
        if not success:
            print("[cycle] ⚠️ Some images failed to push", file=sys.stderr)
    else:
        print("[cycle] Render-only mode — skipping display push", file=sys.stderr)
    
    elapsed = (datetime.now() - start).total_seconds()
    print(f"[cycle] Completed in {elapsed:.1f}s", file=sys.stderr)
    return True


async def main():
    push_to_display = True
    loop_mode = False
    pause = 60
    display_time = 3.0
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--render-only":
            push_to_display = False
        elif args[i] == "--loop":
            loop_mode = True
        elif args[i] == "--pause" and i + 1 < len(args):
            pause = int(args[i + 1])
            i += 1
        elif args[i] == "--display-time" and i + 1 < len(args):
            display_time = float(args[i + 1])
            i += 1
        elif args[i] == "--once":
            pass  # default
        i += 1
    
    if loop_mode:
        print(f"[cycle] Loop mode: every {pause}s, display time {display_time}s", file=sys.stderr)
        while True:
            try:
                await run_cycle(push_to_display, display_time)
            except Exception as e:
                print(f"[cycle] Error: {e}", file=sys.stderr)
            await asyncio.sleep(pause)
    else:
        await run_cycle(push_to_display, display_time)


if __name__ == "__main__":
    asyncio.run(main())
