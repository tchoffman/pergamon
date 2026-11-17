#!/usr/bin/env python3
"""Draw Pergamon's app icons (a library's columned front) as PNGs.

No image libraries needed. Run once after changing the design; the output is
committed under rag/static/icons/.

    python3 scripts/make-icons.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "rag" / "static" / "icons"
BG = (0x8B, 0x45, 0x13)
FG = (0xFA, 0xF7, 0xEE)
SS = 4  # samples per pixel edge, for smooth edges


def _temple(x: float, y: float) -> bool:
    """The building, in unit coordinates, kept inside the central 60% so it
    survives the circle and squircle masks phones apply."""
    if 0.22 <= y <= 0.38:  # roof
        half = (y - 0.22) / 0.16 * 0.30
        if abs(x - 0.5) <= half:
            return True
    if 0.40 <= y <= 0.45 and 0.22 <= x <= 0.78:
        return True
    if 0.47 <= y <= 0.70 and any(abs(x - c) <= 0.035 for c in (0.29, 0.43, 0.57, 0.71)):
        return True
    if 0.72 <= y <= 0.76 and 0.20 <= x <= 0.80:
        return True
    return 0.78 <= y <= 0.82 and 0.17 <= x <= 0.83


def _in_rounded(x: float, y: float, r: float) -> bool:
    dx = max(r - x, 0.0, x - (1 - r))
    dy = max(r - y, 0.0, y - (1 - r))
    return dx * dx + dy * dy <= r * r


def draw(size: int, rounded: bool) -> bytes:
    rows = []
    n = SS * SS
    for py in range(size):
        row = bytearray([0])  # PNG filter type: none
        for px in range(size):
            bg = fg = 0
            for sy in range(SS):
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) / size
                    y = (py + (sy + 0.5) / SS) / size
                    if rounded and not _in_rounded(x, y, 0.2):
                        continue
                    if _temple(x, y):
                        fg += 1
                    else:
                        bg += 1
            cover = bg + fg
            if cover == 0:
                row += bytes(4)
                continue
            rgb = [round((BG[i] * bg + FG[i] * fg) / cover) for i in range(3)]
            row += bytes(rgb + [round(255 * cover / n)])
        rows.append(bytes(row))
    return _png(size, b"".join(rows))


def _png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


ICONS = {
    "icon-32.png": (32, True),
    "icon-192.png": (192, True),
    "icon-512.png": (512, True),
    "icon-maskable-512.png": (512, False),  # the phone cuts its own shape
    "apple-touch-icon.png": (180, False),   # iOS rounds it; must be opaque
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (size, rounded) in ICONS.items():
        (OUT / name).write_bytes(draw(size, rounded))
        print("wrote", OUT / name)
