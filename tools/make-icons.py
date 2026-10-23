#!/usr/bin/env python3
"""Draws the three apps' home-screen icons into public/apps/. Standard library only.

    python3 tools/make-icons.py

Each icon is the house background with one glyph in the app's color:
listen = headphones (blue), speak = microphone (green), admin = sliders (amber).
Sizes: 180 (iPhone), 192 and 512 (Android, PC), and a 512 maskable copy with
the glyph inside the safe zone. Shapes are signed-distance fields, 4x4 supersampled.
"""
import math, os, struct, zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'public', 'apps')
BG = (13, 15, 19)
PANEL = (27, 33, 48)
APPS = {'listen': (120, 170, 255), 'speak': (94, 207, 141), 'admin': (245, 165, 36)}


def png(path, w, h, rows):
    raw = b''.join(b'\x00' + bytes(r) for r in rows)
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


# Signed distances in a unit square (0..1), negative inside.
def rbox(x, y, cx, cy, hw, hh, r):
    qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def ring_arc(x, y, cx, cy, r, t, top_only=True):
    d = abs(math.hypot(x - cx, y - cy) - r) - t
    if top_only and y > cy:
        d = max(d, y - cy)
    return d


def circle(x, y, cx, cy, r):
    return math.hypot(x - cx, y - cy) - r


def glyph(app, x, y):
    if app == 'listen':        # headphones: band + two ear cups
        band = ring_arc(x, y, .5, .54, .25, .035)
        cups = min(rbox(x, y, .27, .62, .065, .11, .05), rbox(x, y, .73, .62, .065, .11, .05))
        return min(band, cups)
    if app == 'speak':         # microphone: capsule, cradle, stand, foot
        cap = rbox(x, y, .5, .40, .095, .17, .095)
        cradle = ring_arc(x, y, .5, .43, .17, .028, top_only=False)
        cradle = max(cradle, .43 - y)
        stand = rbox(x, y, .5, .695, .028, .07, .01)
        foot = rbox(x, y, .5, .77, .12, .028, .028)
        return min(cap, cradle, stand, foot)
    # admin: three sliders with knobs
    d = 9.0
    for row, knob in ((.32, .36), (.5, .64), (.68, .45)):
        d = min(d, rbox(x, y, .5, row, .26, .022, .022), circle(x, y, knob, row, .065))
    return d


def draw(app, size, maskable):
    color = APPS[app]
    ss = 4
    rows = []
    scale = .78 if maskable else 1.0      # maskable: keep the glyph in the 80% safe zone
    for py in range(size):
        row = []
        for px in range(size):
            acc = [0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + .5) / ss) / size
                    y = (py + (sy + .5) / ss) / size
                    gx, gy = .5 + (x - .5) / scale, .5 + (y - .5) / scale
                    tile = rbox(gx, gy, .5, .5, .40, .40, .11)
                    if glyph(app, gx, gy) < 0:
                        c = color
                    elif tile < 0:
                        c = PANEL
                    else:
                        c = BG
                    for i in range(3):
                        acc[i] += c[i]
            row.extend(int(a / (ss * ss) + .5) for a in acc)
        rows.append(row)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    for app in APPS:
        for size, maskable, name in ((180, False, '180'), (192, False, '192'), (512, False, '512'),
                                     (512, True, 'maskable')):
            p = os.path.join(OUT, '%s-%s.png' % (app, name))
            png(p, size, size, draw(app, size, maskable))
            print('wrote', os.path.relpath(p))


if __name__ == '__main__':
    main()
