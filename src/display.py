#!/usr/bin/env python3
"""
Push pixel-art dashboards to the iDotMatrix 64×64 LED display via BLE.
Uses the idotmatrix library's Image module (DIY raw pixel mode).

The display is 64×64 pixels. We render as 64×64 PNGs and send them
directly — the library sends raw PNG data wrapped in BLE protocol chunks.

Key: ConnectionManager is a singleton. We keep one connection open for
the entire sequence to avoid BLE reconnect races.
"""

import asyncio
import io
import struct
import sys
import time
from typing import List, Optional

from PIL import Image as PilImage
from idotmatrix import ConnectionManager

DEVICE_ADDR = "26:C8:1C:3B:99:F5"
DISPLAY_WIDTH = 64
DISPLAY_HEIGHT = 64


class DisplayClient:
    """Manages BLE connection to the iDotMatrix display for image uploads.
    
    Keeps the connection open across multiple image pushes to avoid
    BLE reconnect races that cause 'device not found' errors.
    """
    
    def __init__(self, address: str = DEVICE_ADDR):
        self.address = address
        self.cm: Optional[ConnectionManager] = None
        self._connected = False
    
    async def connect(self):
        """Establish BLE connection and keep it open."""
        self.cm = ConnectionManager()
        self.cm.address = self.address
        await self.cm.connect()
        self._connected = True
        return self
    
    async def disconnect(self):
        """Close BLE connection."""
        if self.cm:
            try:
                await self.cm.disconnect()
            except Exception:
                pass
            self.cm = None
            self._connected = False
    
    async def enter_diy_mode(self, mode: int = 1) -> bool:
        """Enter DIY draw mode (raw pixel mode)."""
        try:
            data = bytearray([5, 0, 4, 1, mode % 256])
            if self.cm:
                await self.cm.send(data=data)
            return True
        except Exception as e:
            print(f"[display] Failed to enter DIY mode: {e}", file=sys.stderr)
            return False
    
    async def upload_image(self, image_path: str) -> bool:
        """Upload a PNG image to the display over the existing connection."""
        try:
            img = PilImage.open(image_path)
            if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
                print(f"[display] WARNING: Image {image_path} is {img.size}, expected {DISPLAY_WIDTH}×{DISPLAY_HEIGHT}",
                      file=sys.stderr)
            
            png_buffer = io.BytesIO()
            img.save(png_buffer, format="PNG")
            png_buffer.seek(0)
            png_data = png_buffer.getvalue()
            
            chunk_size = 4096
            png_chunks = [png_data[i:i+chunk_size] for i in range(0, len(png_data), chunk_size)]
            idk = len(png_data) + len(png_chunks)
            idk_bytes = struct.pack("h", idk)
            png_len_bytes = struct.pack("i", len(png_data))
            
            payloads = bytearray()
            for i, chunk in enumerate(png_chunks):
                payload = (
                    idk_bytes + bytearray([0, 0, 2 if i > 0 else 0]) + png_len_bytes + chunk
                )
                payloads.extend(payload)
            
            if self.cm:
                await self.cm.send(data=payloads)
            
            return True
        except Exception as e:
            print(f"[display] Upload failed for {image_path}: {e}", file=sys.stderr)
            return False
    
    async def push_sequence(self, image_paths: List[str], display_time: float = 3.0,
                            pause_between: float = 0.5) -> bool:
        """Push a sequence of images keeping one BLE connection open."""
        success_count = 0
        
        for attempt in range(3):
            try:
                await self.connect()
                await self.enter_diy_mode()
                break
            except Exception as e:
                print(f"[display] Connection attempt {attempt+1}/3 failed: {e}", file=sys.stderr)
                if attempt < 2:
                    await asyncio.sleep(3 * (attempt + 1))
                else:
                    print("[display] Could not connect to display", file=sys.stderr)
                    return False
        
        for path in image_paths:
            print(f"[display] Pushing {path}...", file=sys.stderr)
            try:
                ok = await self.upload_image(path)
                if ok:
                    success_count += 1
                    await asyncio.sleep(display_time + pause_between)
                else:
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"[display] Error pushing {path}: {e}", file=sys.stderr)
                await asyncio.sleep(1)
        
        await self.disconnect()
        
        print(f"[display] Sequence complete: {success_count}/{len(image_paths)} pushed successfully",
              file=sys.stderr)
        return success_count == len(image_paths)


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <image_path> [image_path...]")
        print(f"       {sys.argv[0]} --sequence dashboard_1_power.png dashboard_2_calories.png ...")
        sys.exit(1)
    
    images = sys.argv[1:]
    if images[0] == "--sequence":
        images = images[1:]
    
    client = DisplayClient()
    await client.push_sequence(images)


if __name__ == "__main__":
    asyncio.run(main())
