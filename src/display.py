#!/usr/bin/env python3
"""
Push pixel-art dashboards to the iDotMatrix 64×64 LED display via BLE.
Uses graffiti mode (setPixel) — the only protocol path that works on this device.

The PNG upload (DIY mode) commands are accepted by the BLE stack but the
display ignores them. Fullscreen color and graffiti pixel commands work,
so we render dashboards pixel-by-pixel.

At 64×64 = 4,096 pixels per dashboard, we batch pixels into groups of ~200
per BLE write to keep it fast (~20 writes per dashboard).
"""

import asyncio
import sys
import time
from typing import List, Optional

from PIL import Image as PilImage
from idotmatrix import ConnectionManager

DEVICE_ADDR = "26:C8:1C:3B:99:F5"
DISPLAY_WIDTH = 64
DISPLAY_HEIGHT = 64
PIXELS_PER_WRITE = 200  # Batch size for graffiti commands


class DisplayClient:
    """Pushes dashboards to the iDotMatrix via graffiti pixel commands."""
    
    def __init__(self, address: str = DEVICE_ADDR):
        self.address = address
        self.cm: Optional[ConnectionManager] = None
    
    async def connect(self):
        self.cm = ConnectionManager()
        self.cm.address = self.address
        await self.cm.connect()
        return self
    
    async def disconnect(self):
        if self.cm:
            try:
                await self.cm.disconnect()
            except Exception:
                pass
            self.cm = None
    
    async def push_image(self, image_path: str) -> bool:
        """Push a 64×64 PNG to the display pixel-by-pixel via graffiti mode."""
        try:
            img = PilImage.open(image_path)
            if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
                print(f"[display] WARNING: {image_path} is {img.size}, expected {DISPLAY_WIDTH}×{DISPLAY_HEIGHT}",
                      file=sys.stderr)
            
            # Convert to RGB if needed
            if img.mode != "RGB":
                img = img.convert("RGB")
            
            pixels = list(img.getdata())
            
            # Build graffiti commands in batches
            total = DISPLAY_WIDTH * DISPLAY_HEIGHT
            batch_count = 0
            
            for start in range(0, total, PIXELS_PER_WRITE):
                batch = bytearray()
                end = min(start + PIXELS_PER_WRITE, total)
                
                for i in range(start, end):
                    r, g, b = pixels[i]
                    x = i % DISPLAY_WIDTH
                    y = i // DISPLAY_WIDTH
                    # Graffiti command: [10, 0, 5, 1, 0, r, g, b, x, y]
                    batch.extend(bytearray([10, 0, 5, 1, 0, r, g, b, x, y]))
                
                if self.cm:
                    await self.cm.send(data=batch)
                batch_count += 1
                # Small delay between batches to avoid overwhelming BLE
                await asyncio.sleep(0.05)
            
            print(f"[display] Pushed {image_path}: {total} pixels in {batch_count} batches",
                  file=sys.stderr)
            return True
            
        except Exception as e:
            print(f"[display] Push failed for {image_path}: {e}", file=sys.stderr)
            return False
    
    async def push_sequence(self, image_paths: List[str], display_time: float = 3.0,
                            pause_between: float = 0.5) -> bool:
        """Push a sequence of images keeping one BLE connection open."""
        success_count = 0
        
        # Connect once
        for attempt in range(3):
            try:
                await self.connect()
                break
            except Exception as e:
                print(f"[display] Connection attempt {attempt+1}/3 failed: {e}", file=sys.stderr)
                if attempt < 2:
                    await asyncio.sleep(3 * (attempt + 1))
                else:
                    print("[display] Could not connect", file=sys.stderr)
                    return False
        
        for path in image_paths:
            print(f"[display] Pushing {path}...", file=sys.stderr)
            try:
                ok = await self.push_image(path)
                if ok:
                    success_count += 1
                    await asyncio.sleep(display_time + pause_between)
                else:
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"[display] Error: {e}", file=sys.stderr)
                await asyncio.sleep(1)
        
        await self.disconnect()
        
        print(f"[display] Sequence: {success_count}/{len(image_paths)} pushed",
              file=sys.stderr)
        return success_count == len(image_paths)


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <image_path> [image_path...]")
        sys.exit(1)
    
    images = sys.argv[1:]
    if images[0] == "--sequence":
        images = images[1:]
    
    client = DisplayClient()
    await client.push_sequence(images)


if __name__ == "__main__":
    asyncio.run(main())
