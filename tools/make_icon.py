"""Draws the program icon (a white shopping cart on the shop's yellow tile) and writes a Windows .ico.
Pure Python, so the build needs no image file or extra package (same method as the Yousef app).

    python tools/make_icon.py <output.ico>
"""
import struct
import sys
import zlib

AMBER, GOLD, INK = (255, 194, 61, 255), (235, 160, 20, 255), (19, 41, 75, 255)


def draw(n):
    px = [[(0, 0, 0, 0)] * n for _ in range(n)]
    r = n * 0.18

    def inside_tile(x, y):
        cx = min(max(x, r), n - 1 - r)
        cy = min(max(y, r), n - 1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r

    def rect(x0, y0, x1, y1, c):
        for y in range(int(y0 * n), max(int(y1 * n), int(y0 * n) + 1)):
            for x in range(int(x0 * n), max(int(x1 * n), int(x0 * n) + 1)):
                px[y][x] = c

    def disc(cx, cy, rad, c):
        for y in range(n):
            for x in range(n):
                if (x - cx * n) ** 2 + (y - cy * n) ** 2 <= (rad * n) ** 2:
                    px[y][x] = c
    for y in range(n):
        for x in range(n):
            if inside_tile(x, y):
                t = y / n
                px[y][x] = tuple(int(AMBER[i] * (1 - t) + GOLD[i] * t) for i in range(3)) + (255,)
    rect(0.16, 0.24, 0.30, 0.30, INK)    # handle
    rect(0.26, 0.30, 0.32, 0.62, INK)    # back of the basket
    rect(0.26, 0.34, 0.84, 0.40, INK)    # top edge
    rect(0.30, 0.56, 0.78, 0.62, INK)    # bottom edge
    rect(0.78, 0.34, 0.84, 0.62, INK)    # front
    rect(0.44, 0.40, 0.48, 0.56, INK)    # slats
    rect(0.60, 0.40, 0.64, 0.56, INK)
    disc(0.38, 0.74, 0.065, INK)          # wheels
    disc(0.72, 0.74, 0.065, INK)
    return px


def png(px):
    n = len(px)
    raw = b''.join(b'\x00' + bytes(v for p in row for v in p) for row in px)

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', n, n, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')


def ico(sizes=(256, 64, 48, 32, 16)):
    imgs = [png(draw(s)) for s in sizes]
    out = struct.pack('<HHH', 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    for s, d in zip(sizes, imgs):
        out += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(d), off)
        off += len(d)
    return out + b''.join(imgs)


if __name__ == '__main__':
    with open(sys.argv[1], 'wb') as f:
        f.write(ico())
