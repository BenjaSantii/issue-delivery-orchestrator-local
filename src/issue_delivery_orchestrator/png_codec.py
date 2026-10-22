from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


@dataclass
class PngImage:
    width: int
    height: int
    pixels: bytearray


def decode_png(payload: bytes) -> PngImage:
    if not payload.startswith(PNG_SIGNATURE):
        raise ValueError("invalid PNG signature")
    chunks: dict[bytes, list[bytes]] = {}
    offset = len(PNG_SIGNATURE)
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        kind = payload[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise ValueError("truncated PNG payload")
        data = payload[offset + 8 : offset + 8 + length]
        expected = struct.unpack(">I", payload[offset + 8 + length : end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != expected:
            raise ValueError(f"invalid {kind.decode(errors='replace')} CRC")
        chunks.setdefault(kind, []).append(data)
        offset = end
        if kind == b"IEND":
            break

    ihdr = b"".join(chunks.get(b"IHDR", []))
    if len(ihdr) != 13:
        raise ValueError("PNG must contain one IHDR chunk")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if not width or not height or width * height > 50_000_000:
        raise ValueError("PNG dimensions are unsafe")
    if depth != 8 or color not in CHANNELS:
        raise ValueError(f"unsupported PNG format: depth={depth}, color={color}")
    if compression or filtering or interlace:
        raise ValueError("compressed, filtered, or interlaced PNG mode is unsupported")

    channels = CHANNELS[color]
    row_size = width * channels
    decoded = zlib.decompress(b"".join(chunks.get(b"IDAT", [])))
    if len(decoded) != height * (row_size + 1):
        raise ValueError("PNG scanline size mismatch")
    rows: list[bytearray] = []
    cursor = 0
    for _ in range(height):
        filter_type = decoded[cursor]
        raw = bytearray(decoded[cursor + 1 : cursor + 1 + row_size])
        cursor += row_size + 1
        previous = rows[-1] if rows else bytearray(row_size)
        _unfilter(raw, previous, channels, filter_type)
        rows.append(raw)

    palette = b"".join(chunks.get(b"PLTE", []))
    transparency = b"".join(chunks.get(b"tRNS", []))
    pixels = bytearray()
    for row in rows:
        for index in range(0, len(row), channels):
            _append_rgba(pixels, row[index : index + channels], color, palette, transparency)
    return PngImage(width, height, pixels)


def png_size(payload: bytes) -> tuple[int, int]:
    if payload[12:16] != b"IHDR":
        raise ValueError("PNG must start with an IHDR chunk")
    return struct.unpack(">II", payload[16:24])


def encode_png(image: PngImage) -> bytes:
    row_size = image.width * 4
    if len(image.pixels) != row_size * image.height:
        raise ValueError("RGBA pixel count does not match dimensions")
    raw = b"".join(
        b"\x00" + bytes(image.pixels[offset : offset + row_size])
        for offset in range(0, len(image.pixels), row_size)
    )
    ihdr = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def _unfilter(row: bytearray, previous: bytearray, bpp: int, kind: int) -> None:
    for index in range(len(row)):
        left = row[index - bpp] if index >= bpp else 0
        above = previous[index]
        upper_left = previous[index - bpp] if index >= bpp else 0
        if kind == 1:
            value = left
        elif kind == 2:
            value = above
        elif kind == 3:
            value = (left + above) // 2
        elif kind == 4:
            value = _paeth(left, above, upper_left)
        elif kind == 0:
            value = 0
        else:
            raise ValueError(f"unsupported PNG filter {kind}")
        row[index] = (row[index] + value) & 0xFF


def _append_rgba(
    target: bytearray,
    sample: bytearray,
    color: int,
    palette: bytes,
    transparency: bytes,
) -> None:
    if color == 6:
        target.extend(sample)
    elif color == 2:
        target.extend((*sample, 255))
    elif color == 0:
        target.extend((sample[0], sample[0], sample[0], 255))
    elif color == 4:
        target.extend((sample[0], sample[0], sample[0], sample[1]))
    else:
        palette_index = sample[0]
        start = palette_index * 3
        if start + 3 > len(palette):
            raise ValueError("PNG palette index is out of bounds")
        alpha = (
            transparency[palette_index]
            if palette_index < len(transparency)
            else 255
        )
        target.extend(
            (*palette[start : start + 3], alpha)
        )


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )
