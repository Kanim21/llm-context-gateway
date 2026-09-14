"""Storage-only reversible codec (.zclaw).

Non-negotiable rule (spec Sec 3.5, 6): this codec lives strictly in the
SQLite storage layer. The model NEVER sees compressed tokens -- content
is always decoded back to its original bytes before it is placed in any
prompt. This module's only job is to shrink what sits on disk. Nothing
downstream of `storage.sqlite_store` may hand a `.zclaw` blob to a
tokenizer or a model.

Codec design: a greedy byte-level n-gram dictionary substitution pass
(learns the most frequent 3-48 byte substrings in the input and replaces
them with a 2-byte reference) followed by zlib DEFLATE for entropy coding
of what remains. It is NOT encryption and makes no such claim -- it is a
transparent, reversible storage transform, nothing more.

Round-trip is exact by construction and verified byte-for-byte by
`verify_round_trip` (used by the test suite and the benchmark runner).
If `decode(encode(x)) != x` that is a bug to fix, not a tuning knob --
this module has no "best effort" mode.
"""

from __future__ import annotations

import hashlib
import zlib
from collections import Counter
from dataclasses import dataclass

MAGIC = b"ZCLAW1\n"
ESCAPE = 0xFF
MIN_NGRAM = 3
MAX_NGRAM = 48
MAX_DICT_ENTRIES = 254  # indices 1..254; index 0 reserved for literal-escape of ESCAPE byte


class CodecIntegrityError(RuntimeError):
    """Raised when a decoded payload fails its length/checksum check."""


@dataclass
class RoundTripResult:
    ok: bool
    original_len: int
    encoded_len: int
    detail: str = ""


def _candidate_ngrams(data: bytes) -> list[bytes]:
    """Pick a dictionary of frequent substrings, longest-savings first."""
    if len(data) < MIN_NGRAM * 2:
        return []
    counts: Counter[bytes] = Counter()
    n = len(data)
    for length in (48, 32, 24, 16, 12, 8, 6, 4, 3):
        if length > n:
            continue
        step = 1
        for i in range(0, n - length + 1, step):
            counts[data[i : i + length]] += 1

    scored: list[tuple[int, bytes]] = []
    for gram, freq in counts.items():
        if freq < 2:
            continue
        savings = (freq - 1) * len(gram) - freq * 2  # net bytes saved vs 2-byte reference
        if savings > 0:
            scored.append((savings, gram))
    scored.sort(key=lambda t: (-t[0], -len(t[1])))

    chosen: list[bytes] = []
    for _, gram in scored:
        if len(chosen) >= MAX_DICT_ENTRIES:
            break
        if any(gram in c or c in gram for c in chosen):
            continue
        chosen.append(gram)
    chosen.sort(key=len, reverse=True)
    return chosen


def _substitute(data: bytes, dictionary: list[bytes]) -> bytes:
    out = bytearray()
    n = len(data)
    i = 0
    lookup = [(idx + 1, gram) for idx, gram in enumerate(dictionary)]
    while i < n:
        byte = data[i]
        if byte == ESCAPE:
            out.append(ESCAPE)
            out.append(0)
            i += 1
            continue
        matched = False
        for idx, gram in lookup:
            gl = len(gram)
            if data[i : i + gl] == gram:
                out.append(ESCAPE)
                out.append(idx)
                i += gl
                matched = True
                break
        if not matched:
            out.append(byte)
            i += 1
    return bytes(out)


def _desubstitute(body: bytes, dictionary: list[bytes]) -> bytes:
    out = bytearray()
    n = len(body)
    i = 0
    while i < n:
        byte = body[i]
        if byte == ESCAPE:
            idx = body[i + 1]
            if idx == 0:
                out.append(ESCAPE)
            else:
                out.extend(dictionary[idx - 1])
            i += 2
        else:
            out.append(byte)
            i += 1
    return bytes(out)


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _read_varint(buf: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        byte = buf[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return value, offset


def _pack_dictionary(dictionary: list[bytes]) -> bytes:
    out = bytearray()
    out += _write_varint(len(dictionary))
    for gram in dictionary:
        out += _write_varint(len(gram))
        out += gram
    return bytes(out)


def _unpack_dictionary(buf: bytes, offset: int) -> tuple[list[bytes], int]:
    count, offset = _read_varint(buf, offset)
    dictionary = []
    for _ in range(count):
        length, offset = _read_varint(buf, offset)
        dictionary.append(buf[offset : offset + length])
        offset += length
    return dictionary, offset


def encode(data: bytes) -> bytes:
    """Compress `data` into a self-describing .zclaw blob."""
    dictionary = _candidate_ngrams(data)
    substituted = _substitute(data, dictionary)
    packed = _pack_dictionary(dictionary) + substituted
    deflated = zlib.compress(packed, level=9)
    checksum = hashlib.sha256(data).digest()
    header = MAGIC + len(data).to_bytes(8, "big") + checksum
    return header + deflated


def decode(blob: bytes) -> bytes:
    """Decompress a .zclaw blob back to the original bytes, or raise."""
    if blob[: len(MAGIC)] != MAGIC:
        raise CodecIntegrityError("bad magic: not a .zclaw blob")
    offset = len(MAGIC)
    original_len = int.from_bytes(blob[offset : offset + 8], "big")
    offset += 8
    checksum = blob[offset : offset + 32]
    offset += 32
    deflated = blob[offset:]
    packed = zlib.decompress(deflated)
    dictionary, body_offset = _unpack_dictionary(packed, 0)
    substituted = packed[body_offset:]
    original = _desubstitute(substituted, dictionary)

    if len(original) != original_len:
        raise CodecIntegrityError(
            f"length mismatch after decode: expected {original_len}, got {len(original)}"
        )
    if hashlib.sha256(original).digest() != checksum:
        raise CodecIntegrityError("checksum mismatch after decode: data is not byte-identical")
    return original


def encode_text(text: str) -> bytes:
    return encode(text.encode("utf-8"))


def decode_text(blob: bytes) -> str:
    return decode(blob).decode("utf-8")


def verify_round_trip(data: bytes) -> RoundTripResult:
    """Encode then decode `data` and confirm byte-identical recovery."""
    try:
        blob = encode(data)
        recovered = decode(blob)
    except Exception as exc:  # noqa: BLE001 - report, don't hide
        return RoundTripResult(ok=False, original_len=len(data), encoded_len=-1, detail=str(exc))
    ok = recovered == data
    return RoundTripResult(
        ok=ok,
        original_len=len(data),
        encoded_len=len(blob),
        detail="" if ok else "byte mismatch after decode",
    )
