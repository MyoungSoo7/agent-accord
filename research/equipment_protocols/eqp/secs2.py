"""SECS-II item codec (SEMI E5 data item encoding), stdlib only.

Wire format of one item::

    format byte = (format_code << 2) | number_of_length_bytes   (1..3)
    length      = big-endian, number_of_length_bytes long
                  (List: number of child items; everything else: number of data bytes)
    data

Format codes are given in octal, as in E5.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


class Fmt(IntEnum):
    L = 0o00
    B = 0o10
    BOOLEAN = 0o11
    A = 0o20
    J = 0o21  # JIS-8; decoded as latin-1 here (not needed by the study)
    I8 = 0o30
    I1 = 0o31
    I2 = 0o32
    I4 = 0o34
    F8 = 0o40
    F4 = 0o44
    U8 = 0o50
    U1 = 0o51
    U2 = 0o52
    U4 = 0o54


# numeric formats: struct code + size
_NUM: dict[Fmt, tuple[str, int]] = {
    Fmt.I8: ("q", 8),
    Fmt.I1: ("b", 1),
    Fmt.I2: ("h", 2),
    Fmt.I4: ("i", 4),
    Fmt.F8: ("d", 8),
    Fmt.F4: ("f", 4),
    Fmt.U8: ("Q", 8),
    Fmt.U1: ("B", 1),
    Fmt.U2: ("H", 2),
    Fmt.U4: ("I", 4),
}
_BY_CODE = {f.value: f for f in Fmt}
MAX_LENGTH = 0xFFFFFF


class SecsDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Item:
    """One SECS-II item.

    value is: tuple[Item, ...] for L, bytes for B, str for A/J,
    tuple[bool, ...] for BOOLEAN, tuple[int|float, ...] for numeric formats
    (SECS-II numeric items are arrays; a scalar is an array of length 1).
    """

    fmt: Fmt
    value: object

    # convenience accessors -------------------------------------------------
    @property
    def items(self) -> tuple["Item", ...]:
        if self.fmt is not Fmt.L:
            raise TypeError(f"{self.fmt.name} is not a list")
        return self.value  # type: ignore[return-value]

    @property
    def scalar(self):
        """First element of a numeric/boolean array, the str of A, or the first byte of B."""
        if self.fmt is Fmt.L:
            raise TypeError("list has no scalar value")
        if self.fmt in (Fmt.A, Fmt.J):
            return self.value
        if self.fmt is Fmt.B:
            return self.value[0]  # type: ignore[index]
        return self.value[0]  # type: ignore[index]

    def __len__(self) -> int:  # number of elements as carried in the length field semantics
        if self.fmt is Fmt.L:
            return len(self.value)  # type: ignore[arg-type]
        return len(self.value)  # type: ignore[arg-type]


# constructors --------------------------------------------------------------

def L(*items: Item) -> Item:
    return Item(Fmt.L, tuple(items))


def B(data: bytes | Iterable[int]) -> Item:
    return Item(Fmt.B, bytes(data))


def BOOLEAN(*vals: bool) -> Item:
    return Item(Fmt.BOOLEAN, tuple(bool(v) for v in vals))


def A(text: str) -> Item:
    return Item(Fmt.A, text)


def _num(fmt: Fmt):
    def make(*vals) -> Item:
        return Item(fmt, tuple(vals))

    make.__name__ = fmt.name
    return make


I1, I2, I4, I8 = _num(Fmt.I1), _num(Fmt.I2), _num(Fmt.I4), _num(Fmt.I8)
U1, U2, U4, U8 = _num(Fmt.U1), _num(Fmt.U2), _num(Fmt.U4), _num(Fmt.U8)
F4, F8 = _num(Fmt.F4), _num(Fmt.F8)


# encoding ------------------------------------------------------------------

def item_header(fmt: Fmt, length: int) -> bytes:
    """Format byte + 1..3 length bytes (the minimum that fits)."""
    if length < 0 or length > MAX_LENGTH:
        raise ValueError(f"item length {length} out of range")
    if length <= 0xFF:
        return bytes((fmt << 2 | 1, length))
    if length <= 0xFFFF:
        return bytes((fmt << 2 | 2,)) + length.to_bytes(2, "big")
    return bytes((fmt << 2 | 3,)) + length.to_bytes(3, "big")


def _encode_into(item: Item, out: list[bytes]) -> None:
    fmt = item.fmt
    if fmt is Fmt.L:
        children = item.value
        out.append(item_header(fmt, len(children)))  # type: ignore[arg-type]
        for child in children:  # type: ignore[union-attr]
            _encode_into(child, out)
        return
    if fmt is Fmt.A:
        data = item.value.encode("ascii")  # type: ignore[union-attr]
    elif fmt is Fmt.J:
        data = item.value.encode("latin-1")  # type: ignore[union-attr]
    elif fmt is Fmt.B:
        data = item.value  # type: ignore[assignment]
    elif fmt is Fmt.BOOLEAN:
        data = bytes(1 if v else 0 for v in item.value)  # type: ignore[union-attr]
    else:
        code, _size = _NUM[fmt]
        vals = item.value
        data = struct.pack(f">{len(vals)}{code}", *vals)  # type: ignore[arg-type]
    out.append(item_header(fmt, len(data)))
    out.append(data)


def encode(item: Item) -> bytes:
    out: list[bytes] = []
    _encode_into(item, out)
    return b"".join(out)


# decoding ------------------------------------------------------------------

def decode_from(buf: bytes | memoryview, pos: int = 0) -> tuple[Item, int]:
    mv = memoryview(buf)
    end = len(mv)
    if pos >= end:
        raise SecsDecodeError("no item at offset %d" % pos)
    fb = mv[pos]
    nlen = fb & 0x03
    code = fb >> 2
    if nlen == 0:
        raise SecsDecodeError(f"format byte 0x{fb:02x}: zero length bytes")
    fmt = _BY_CODE.get(code)
    if fmt is None:
        raise SecsDecodeError(f"unknown format code 0o{code:o}")
    if pos + 1 + nlen > end:
        raise SecsDecodeError("truncated length field")
    length = int.from_bytes(mv[pos + 1 : pos + 1 + nlen], "big")
    pos += 1 + nlen
    if fmt is Fmt.L:
        children = []
        for _ in range(length):
            child, pos = decode_from(mv, pos)
            children.append(child)
        return Item(fmt, tuple(children)), pos
    if pos + length > end:
        raise SecsDecodeError(f"{fmt.name}: needs {length} data bytes, {end - pos} left")
    data = bytes(mv[pos : pos + length])
    pos += length
    if fmt is Fmt.A:
        return Item(fmt, data.decode("ascii")), pos
    if fmt is Fmt.J:
        return Item(fmt, data.decode("latin-1")), pos
    if fmt is Fmt.B:
        return Item(fmt, data), pos
    if fmt is Fmt.BOOLEAN:
        return Item(fmt, tuple(b != 0 for b in data)), pos
    code_s, size = _NUM[fmt]
    if length % size:
        raise SecsDecodeError(f"{fmt.name}: length {length} not a multiple of {size}")
    return Item(fmt, struct.unpack(f">{length // size}{code_s}", data)), pos


def decode(buf: bytes) -> Item:
    item, pos = decode_from(buf, 0)
    if pos != len(buf):
        raise SecsDecodeError(f"{len(buf) - pos} trailing bytes after item")
    return item


# SML-ish text, for logs and debugging ---------------------------------------

def to_sml(item: Item, indent: int = 0) -> str:
    pad = "  " * indent
    if item.fmt is Fmt.L:
        inner = "".join("\n" + to_sml(c, indent + 1) for c in item.items)
        return f"{pad}<L[{len(item.items)}]{inner}\n{pad}>"
    if item.fmt in (Fmt.A, Fmt.J):
        return f'{pad}<{item.fmt.name} "{item.value}">'
    if item.fmt is Fmt.B:
        return f"{pad}<B " + " ".join(f"0x{b:02X}" for b in item.value) + ">"  # type: ignore[union-attr]
    vals = " ".join(str(v) for v in item.value)  # type: ignore[union-attr]
    return f"{pad}<{item.fmt.name} {vals}>"
