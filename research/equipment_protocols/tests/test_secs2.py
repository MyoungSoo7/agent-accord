"""SECS-II codec against hand-computed encodings (SEMI E5 format codes, octal) and secsgem."""

import struct

import pytest

from eqp import secs2
from eqp.secs2 import Fmt
from eqp.gem import S6F11Encoder
from eqp.workload import EventSource, var_specs


@pytest.mark.parametrize(
    "item, expected",
    [
        (secs2.U4(1), b"\xb1\x04\x00\x00\x00\x01"),
        (secs2.A("AB"), b"\x41\x02AB"),
        (secs2.L(), b"\x01\x00"),
        (secs2.B([0]), b"\x21\x01\x00"),
        (secs2.BOOLEAN(True), b"\x25\x01\x01"),
        (secs2.I1(-1), b"\x65\x01\xff"),
        (secs2.I2(-2), b"\x69\x02\xff\xfe"),
        (secs2.I4(1), b"\x71\x04\x00\x00\x00\x01"),
        (secs2.I8(-2), b"\x61\x08" + b"\xff" * 7 + b"\xfe"),
        (secs2.U1(255), b"\xa5\x01\xff"),
        (secs2.U2(258), b"\xa9\x02\x01\x02"),
        (secs2.U8(1), b"\xa1\x08" + b"\x00" * 7 + b"\x01"),
        (secs2.F4(1.0), b"\x91\x04\x3f\x80\x00\x00"),
        (secs2.F8(1.5), b"\x81\x08\x3f\xf8" + b"\x00" * 6),
        (secs2.U2(1, 2), b"\xa9\x04\x00\x01\x00\x02"),  # arrays: length counts bytes
        (secs2.A(""), b"\x41\x00"),
    ],
)
def test_hand_computed_encodings(item, expected):
    assert secs2.encode(item) == expected
    assert secs2.decode(expected) == item


def test_format_codes_are_octal_and_shifted():
    # format byte = code << 2 | number of length bytes
    table = {Fmt.L: 0x01, Fmt.B: 0x21, Fmt.BOOLEAN: 0x25, Fmt.A: 0x41, Fmt.J: 0x45, Fmt.I8: 0x61,
             Fmt.I1: 0x65, Fmt.I2: 0x69, Fmt.I4: 0x71, Fmt.F8: 0x81, Fmt.F4: 0x91, Fmt.U8: 0xA1,
             Fmt.U1: 0xA5, Fmt.U2: 0xA9, Fmt.U4: 0xB1}
    for fmt, fb in table.items():
        assert secs2.item_header(fmt, 0)[0] == fb, fmt


def test_two_item_list_prefix_and_list_length_counts_items():
    enc = secs2.encode(secs2.L(secs2.U4(1), secs2.A("AB")))
    assert enc.startswith(b"\x01\x02")
    assert enc == b"\x01\x02" + b"\xb1\x04\x00\x00\x00\x01" + b"\x41\x02AB"


def test_multi_byte_lengths():
    assert secs2.encode(secs2.A("x" * 300))[:3] == b"\x42\x01\x2c"
    assert secs2.encode(secs2.B(bytes(70000)))[:4] == b"\x23\x01\x11\x70"
    big_list = secs2.L(*[secs2.U1(0)] * 256)
    assert secs2.encode(big_list)[:3] == b"\x02\x01\x00"
    assert secs2.decode(secs2.encode(big_list)) == big_list
    with pytest.raises(ValueError):
        secs2.item_header(Fmt.B, 0x1000000)


def test_nested_round_trip():
    tree = secs2.L(secs2.U4(7), secs2.L(secs2.F8(-0.25, 3.5), secs2.A("LOT00001.01"), secs2.BOOLEAN(False, True)),
                   secs2.L(), secs2.B(b"\x00\xff"), secs2.I8(-(2**63)), secs2.U8(2**64 - 1))
    assert secs2.decode(secs2.encode(tree)) == tree


@pytest.mark.parametrize(
    "raw",
    [
        b"\xb0\x04\x00\x00\x00\x01",  # zero length bytes
        b"\xb1\x04\x00\x00",  # truncated data
        b"\xb1",  # truncated length
        b"\xb1\x03\x00\x00\x01",  # U4 with 3 bytes
        b"\xfd\x00",  # unknown format code 0o77
        b"\x01\x02\xb1\x04\x00\x00\x00\x01",  # list missing its 2nd child
        b"\x41\x01A\x00",  # trailing byte
    ],
)
def test_decode_errors(raw):
    with pytest.raises(secs2.SecsDecodeError):
        secs2.decode(raw)


@pytest.mark.parametrize("n", [1, 10, 50])
def test_fast_s6f11_encoder_matches_generic_codec(n):
    specs = var_specs(n)
    enc = S6F11Encoder(specs)
    values = EventSource(n, seed=3).make(41)
    assert enc.encode(41, values) == secs2.encode(enc.tree(41, values))


def test_s6f11_size_formula():
    # L3 + U4 DATAID + U4 CEID + L1 + L2 + U4 RPTID + L(n+2) + values
    n = 10
    specs = var_specs(n)
    values = EventSource(n, seed=1).make(0)
    body = S6F11Encoder(specs).encode(0, values)
    sizes = {"U4": 6, "U8": 10, "F8": 10}
    expected = 2 + 6 + 6 + 2 + 2 + 6 + 2 + sum(sizes.get(s.kind, 2 + 11) for s in specs)
    assert len(body) == expected


secsgem_vars = pytest.importorskip("secsgem.secs.variables")


def test_cross_check_with_secsgem_items():
    sv = secsgem_vars
    assert sv.U4(1).encode() == secs2.encode(secs2.U4(1))
    assert sv.String("AB").encode() == secs2.encode(secs2.A("AB"))
    assert sv.F8(1.5).encode() == secs2.encode(secs2.F8(1.5))
    assert sv.I8(-2).encode() == secs2.encode(secs2.I8(-2))
    assert sv.Binary(b"\x01\x02").encode() == secs2.encode(secs2.B(b"\x01\x02"))
    assert sv.Boolean(True).encode() == secs2.encode(secs2.BOOLEAN(True))
    assert sv.String("x" * 300).encode() == secs2.encode(secs2.A("x" * 300))


def test_cross_check_with_secsgem_s6f11():
    import secsgem.secs

    sv = secsgem_vars
    n = 10
    specs = var_specs(n)
    values = EventSource(n, seed=5).make(9)
    conv = {"U4": sv.U4, "U8": sv.U8, "F8": sv.F8, "A": sv.String}
    f = secsgem.secs.functions.SecsS06F11({
        "DATAID": sv.U4(9), "CEID": sv.U4(5001),
        "RPT": [{"RPTID": sv.U4(4001), "V": [conv[s.kind](v) for s, v in zip(specs, values)]}],
    })
    assert f.encode() == S6F11Encoder(specs).encode(9, values)
    assert struct.unpack(">d", f.encode()[2 + 6 + 6 + 2 + 2 + 6 + 2 + 6 + 10 + 2: 2 + 6 + 6 + 2 + 2 + 6 + 2 + 6 + 10 + 10])[0] == values[2]
