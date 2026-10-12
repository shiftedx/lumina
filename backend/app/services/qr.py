"""A minimal QR Code encoder (byte mode, error correction M) for the two-step enrollment QR, so no image service ever
sees the shared secret. A compact port of Project Nayuki's reference algorithm (MIT): ISO/IEC 18004 versions 1-40,
all eight masks scored by the standard penalty rules.
"""
from __future__ import annotations

# Level M, indexed by version (index 0 unused).
_ECC_PER_BLOCK = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28,
                  28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28)
_BLOCKS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33,
           35, 37, 38, 40, 43, 45, 47, 49)
_FORMAT_BITS_M = 0  # error correction level M's 2-bit format indicator


def _raw_modules(ver: int) -> int:
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        result -= (25 * align - 10) * align - 55
        if ver >= 7:
            result -= 36
    return result


def _data_codewords(ver: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _BLOCKS[ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _alignment_positions(ver: int) -> list[int]:
    if ver == 1:
        return []
    count = ver // 7 + 2
    step = (ver * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return [6] + [ver * 4 + 10 - i * step for i in range(count - 1)][::-1]


def encode(text: str) -> list[list[bool]]:
    """The module matrix (True = dark) for ``text`` as UTF-8 bytes. Raises ValueError when it cannot fit."""
    data = text.encode("utf-8")
    for ver in range(1, 41):
        count_bits = 8 if ver <= 9 else 16
        if 4 + count_bits + len(data) * 8 <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("Text too long for a QR code.")
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    capacity = _data_codewords(ver) * 8
    put(0, min(4, capacity - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    codewords = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    # Split into blocks, append Reed-Solomon ECC, interleave.
    blocks_n, ecc_len = _BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    short_count = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    divisor = _rs_divisor(ecc_len)
    blocks, k = [], 0
    for i in range(blocks_n):
        size = short_len - ecc_len + (0 if i < short_count else 1)
        dat = codewords[k:k + size]
        k += size
        ecc = _rs_remainder(dat, divisor)
        if i < short_count:
            dat = dat + [-1]  # placeholder keeps short blocks aligned with long ones
        blocks.append(dat + ecc)
    final = [block[i] for i in range(len(blocks[0])) for block in blocks if block[i] != -1]

    size = ver * 4 + 17
    modules = [[False] * size for _ in range(size)]
    function = [[False] * size for _ in range(size)]

    def setf(x: int, y: int, dark: bool) -> None:
        modules[y][x] = dark
        function[y][x] = True

    for i in range(size):  # timing patterns
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):  # finders with separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    positions = _alignment_positions(ver)
    last = len(positions) - 1
    for i, ax in enumerate(positions):
        for j, ay in enumerate(positions):
            if (i, j) in ((0, 0), (0, last), (last, 0)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask: int) -> None:
        value = _FORMAT_BITS_M << 3 | mask
        rem = value
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fbits = (value << 10 | rem) ^ 0x5412
        bit = lambda i: ((fbits >> i) & 1) != 0  # noqa: E731
        for i in range(6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6))
        setf(8, 8, bit(7))
        setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)  # the dark module

    draw_format(0)  # reserve the format areas
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        vbits = ver << 12 | rem
        for i in range(18):
            dark = ((vbits >> i) & 1) != 0
            a, b = size - 11 + i % 3, i // 3
            setf(a, b, dark)
            setf(b, a, dark)

    # Zigzag data placement.
    i = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not function[y][x] and i < len(final) * 8:
                    modules[y][x] = ((final[i >> 3] >> (7 - (i & 7))) & 1) != 0
                    i += 1
        right -= 2

    masks = (
        lambda x, y: (x + y) % 2 == 0,
        lambda x, y: y % 2 == 0,
        lambda x, y: x % 3 == 0,
        lambda x, y: (x + y) % 3 == 0,
        lambda x, y: (x // 3 + y // 2) % 2 == 0,
        lambda x, y: x * y % 2 + x * y % 3 == 0,
        lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
        lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
    )

    def apply(mask: int) -> None:
        for y in range(size):
            for x in range(size):
                if not function[y][x] and masks[mask](x, y):
                    modules[y][x] = not modules[y][x]

    best, best_score = 0, None
    for mask in range(8):
        apply(mask)
        draw_format(mask)
        score = _penalty(modules)
        if best_score is None or score < best_score:
            best, best_score = mask, score
        apply(mask)  # XOR again undoes it
    apply(best)
    draw_format(best)
    return modules


def _penalty(modules: list[list[bool]]) -> int:
    size = len(modules)
    score = 0
    lines = modules + [list(column) for column in zip(*modules)]
    finder_a = [True, False, True, True, True, False, True, False, False, False, False]
    finder_b = finder_a[::-1]
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        padded = [False] * 4 + line + [False] * 4
        for i in range(len(padded) - 10):
            window = padded[i:i + 11]
            if window == finder_a or window == finder_b:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, modules))
    total = size * size
    score += (-(-abs(dark * 20 - total * 10) // total) - 1) * 10
    return score


def svg_path(text: str, border: int = 4) -> tuple[int, str]:
    """(viewBox size including the quiet zone, SVG path data drawing every dark module as a 1x1 square)."""
    modules = encode(text)
    parts = [f"M{x + border} {y + border}h1v1h-1z" for y, row in enumerate(modules) for x, dark in enumerate(row) if dark]
    return len(modules) + 2 * border, "".join(parts)
