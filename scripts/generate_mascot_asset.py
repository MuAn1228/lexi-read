"""Original Lexi pixel sprite. Hand-authored integer pixels; standard library only.

No reference-project artwork is used. The PNG and editable text grid are
deterministic outputs of this source, without antialiasing or external services.
"""
from pathlib import Path
import struct
import zlib

WIDTH, HEIGHT = 64, 72
PALETTE = {
    '.': (0, 0, 0, 0),
    'o': (35, 41, 65, 255),
    'h': (54, 65, 103, 255),
    'l': (82, 97, 136, 255),
    'd': (43, 53, 82, 255),
    's': (239, 204, 180, 255),
    't': (209, 158, 148, 255),
    'w': (245, 243, 234, 255),
    'v': (196, 207, 211, 255),
    'g': (62, 111, 102, 255),
    'e': (125, 167, 150, 255),
    'p': (209, 102, 153, 255),
    'P': (158, 53, 108, 255),
    'a': (255, 250, 236, 255),
}
pixels = [['.'] * WIDTH for _ in range(HEIGHT)]


def rect(x0, y0, x1, y1, color):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            pixels[y][x] = color


# The bob: stepped silhouette, opaque navy outline, three flat color clusters.
for bounds in [(23, 4, 41, 5), (19, 6, 45, 7), (17, 8, 47, 10),
               (15, 11, 49, 27), (17, 28, 47, 31), (20, 32, 44, 33)]:
    rect(*bounds, 'o')
for bounds in [(23, 6, 41, 7), (19, 8, 45, 10), (17, 11, 47, 25),
               (19, 26, 45, 29), (22, 30, 42, 31)]:
    rect(*bounds, 'h')
rect(24, 8, 38, 9, 'l')
rect(21, 10, 31, 11, 'l')
rect(19, 12, 23, 14, 'l')
rect(42, 12, 45, 16, 'l')

# Face turned a little toward the page (left when docked on the right).
rect(22, 16, 42, 25, 't')
rect(24, 26, 40, 28, 't')
rect(23, 15, 41, 24, 's')
rect(25, 25, 39, 27, 's')
rect(21, 18, 22, 22, 's')
rect(21, 12, 41, 16, 'h')
rect(21, 16, 24, 18, 'h')
rect(32, 15, 35, 17, 'h')
rect(39, 16, 43, 20, 'h')
rect(18, 15, 20, 26, 'h')
rect(43, 17, 46, 27, 'h')
rect(21, 25, 22, 28, 'h')
rect(42, 26, 44, 29, 'h')
rect(25, 20, 28, 20, 'o')
rect(35, 20, 38, 20, 'o')
rect(26, 21, 27, 23, 'P')
rect(36, 21, 37, 23, 'P')
rect(26, 21, 26, 21, 'p')
rect(36, 21, 36, 21, 'p')
rect(26, 22, 26, 22, 'a')
rect(36, 22, 36, 22, 'a')
rect(30, 26, 32, 26, 't')
# A tiny AI receiver, not a large gaming headset.
rect(46, 19, 49, 25, 'o')
rect(47, 20, 48, 24, 'v')
rect(48, 21, 48, 22, 'p')

# White long-sleeved jacket, navy collar, small pink clasp.
rect(28, 29, 36, 33, 'o')
rect(30, 29, 34, 31, 's')
rect(24, 32, 41, 34, 'o')
rect(21, 35, 44, 39, 'o')
rect(19, 38, 46, 45, 'o')
rect(22, 44, 43, 53, 'o')
rect(24, 54, 41, 55, 'o')
rect(25, 34, 40, 36, 'w')
rect(23, 36, 42, 48, 'w')
rect(24, 49, 41, 52, 'w')
rect(24, 53, 41, 53, 'v')
rect(21, 39, 22, 44, 'v')
rect(43, 39, 44, 44, 'v')
rect(25, 33, 28, 34, 'd')
rect(28, 35, 30, 36, 'd')
rect(36, 33, 39, 34, 'd')
rect(34, 35, 36, 36, 'd')
rect(31, 36, 33, 38, 'g')
rect(32, 37, 32, 37, 'p')
rect(25, 50, 27, 51, 'g')
rect(38, 50, 40, 51, 'v')

# Navy trousers and low, flat boots, with a readable two-pixel gap.
rect(26, 54, 39, 57, 'o')
rect(26, 58, 31, 65, 'o')
rect(34, 58, 39, 65, 'o')
rect(27, 55, 31, 63, 'h')
rect(34, 55, 38, 63, 'd')
rect(24, 65, 31, 67, 'o')
rect(34, 65, 41, 67, 'o')
rect(25, 65, 29, 65, 'l')
rect(35, 65, 39, 65, 'h')

# Open book. Asymmetric, stepped pages give the idle sprite its reading pose.
rect(16, 39, 21, 43, 'o')
rect(18, 40, 23, 44, 'w')
rect(42, 39, 46, 43, 'o')
rect(42, 40, 44, 44, 'w')
rect(16, 40, 29, 49, 'o')
rect(30, 38, 43, 48, 'o')
rect(18, 41, 28, 47, 'a')
rect(31, 39, 41, 46, 'w')
rect(18, 48, 29, 49, 'g')
rect(30, 47, 42, 48, 'g')
rect(29, 41, 30, 49, 'g')
rect(19, 43, 25, 43, 'v')
rect(20, 45, 26, 45, 'v')
rect(33, 41, 39, 41, 'v')
rect(33, 43, 38, 43, 'v')
rect(33, 45, 39, 45, 'v')
rect(31, 48, 34, 49, 'e')
rect(34, 48, 35, 51, 'p')
# Hands rest on the outside of the covers, not over the text.
rect(15, 43, 17, 46, 't')
rect(15, 43, 16, 45, 's')
rect(42, 43, 45, 46, 't')
rect(43, 43, 45, 45, 's')


def chunk(kind, content):
    return struct.pack('!I', len(content)) + kind + content + struct.pack('!I', zlib.crc32(kind + content))


asset_dir = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'mascot' / 'assets'
asset_dir.mkdir(parents=True, exist_ok=True)
raw = b''.join(b'\0' + bytes(channel for key in row for channel in PALETTE[key]) for row in pixels)
png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', WIDTH, HEIGHT, 8, 6, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
(asset_dir / 'idle.png').write_bytes(png)
(asset_dir / 'idle.pixels.txt').write_text('\n'.join(''.join(row) for row in pixels) + '\n', encoding='utf-8')
print('Wrote original 64 x 72 RGBA pixel sprite and editable grid.')
