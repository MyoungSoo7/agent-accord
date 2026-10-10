"""HSMS header/framing (hand-computed + secsgem cross-check) and live loopback behaviour."""

import asyncio

import pytest

from eqp import hsms, secs2


def test_select_req_frame():
    frame = hsms.encode_frame(hsms.control_header(hsms.SELECT_REQ, 5))
    assert frame == b"\x00\x00\x00\x0a" + b"\xff\xff\x00\x00\x00\x01\x00\x00\x00\x05"


def test_data_frame_wbit_stream_function_system():
    body = secs2.encode(secs2.L())
    hdr = hsms.data_header(6, 11, 0x01020304, wbit=True, session_id=0)
    frame = hsms.encode_frame(hdr, body)
    assert frame[:4] == b"\x00\x00\x00\x0c"  # 10 header + 2 body
    assert frame[4:14] == b"\x00\x00\x86\x0b\x00\x00\x01\x02\x03\x04"
    reply = hsms.data_header(6, 12, 0x01020304, wbit=False)
    assert reply.encode() == b"\x00\x00\x06\x0c\x00\x00\x01\x02\x03\x04"
    dec = hsms.Header.decode(frame[4:14])
    assert (dec.stream, dec.function, dec.wbit, dec.system, dec.stype) == (6, 11, True, 0x01020304, hsms.DATA)


def test_select_rsp_and_reject_layout():
    rsp = hsms.control_header(hsms.SELECT_RSP, 9, byte3=hsms.SELECT_ALREADY_ACTIVE)
    assert rsp.encode() == b"\xff\xff\x00\x01\x00\x02\x00\x00\x00\x09"
    rej = hsms.control_header(hsms.REJECT_REQ, 9, byte2=hsms.DATA, byte3=hsms.REJECT_NOT_SELECTED)
    assert rej.encode() == b"\xff\xff\x00\x04\x00\x07\x00\x00\x00\x09"


def test_header_validation():
    with pytest.raises(ValueError):
        hsms.data_header(128, 1, 1, wbit=False)
    with pytest.raises(ValueError):
        hsms.Header.decode(b"\x00" * 9)


secsgem_hsms = pytest.importorskip("secsgem.hsms")


def test_cross_check_with_secsgem_frames():
    sh = secsgem_hsms
    m = sh.HsmsMessage(sh.HsmsStreamFunctionHeader(system=0x01020304, stream=6, function=11,
                                                    require_response=True, session_id=0), b"\x01\x00")
    assert m.blocks[0].encode() == hsms.encode_frame(hsms.data_header(6, 11, 0x01020304, wbit=True), b"\x01\x00")
    for hdr_cls, stype in ((sh.HsmsSelectReqHeader, hsms.SELECT_REQ), (sh.HsmsLinktestReqHeader, hsms.LINKTEST_REQ),
                           (sh.HsmsSelectRspHeader, hsms.SELECT_RSP)):
        theirs = sh.HsmsMessage(hdr_cls(77), b"").blocks[0].encode()
        assert theirs == hsms.encode_frame(hsms.control_header(stype, 77)), hdr_cls


# ---------------------------------------------------------------- live loopback

async def _pair(on_primary=None, t3=45.0):
    passive: list[hsms.HsmsConnection] = []

    async def accept(r, w):
        c = hsms.HsmsConnection(r, w, active=False, on_primary=on_primary, t7=2.0)
        passive.append(c)
        c.start()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    active = await hsms.connect("127.0.0.1", port, t3=t3)
    return server, active, passive[0]


def test_request_reply_linktest_and_t3():
    async def main():
        async def echo(conn, msg):
            if msg.function == 1:
                conn.reply(msg, 2, msg.body)
            # S1F3 is deliberately never answered -> T3

        server, active, passive = await _pair(echo, t3=0.2)
        assert active.selected.is_set() and passive.selected.is_set()
        body = secs2.encode(secs2.A("ping"))
        r1 = await active.request(1, 1, body)
        r2 = await active.request(1, 1, body)
        assert (r1.stream, r1.function, r1.wbit) == (1, 2, False)
        assert r1.body == body and r2.system == r1.system + 1
        await active.linktest()
        with pytest.raises(hsms.T3Timeout):
            await active.request(1, 3)
        await active.separate()
        await asyncio.wait_for(passive.wait_closed(), 1.0)
        server.close()

    asyncio.run(main())


def test_data_before_select_is_rejected():
    async def main():
        got = []

        async def accept(r, w):
            c = hsms.HsmsConnection(r, w, active=False, t7=2.0)
            c.start()

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(hsms.encode_frame(hsms.data_header(1, 1, 42, wbit=True)))
        length = int.from_bytes(await reader.readexactly(4), "big")
        hdr = hsms.Header.decode(await reader.readexactly(length))
        got.append(hdr)
        writer.close()
        server.close()
        return got[0]

    hdr = asyncio.run(main())
    assert hdr.stype == hsms.REJECT_REQ and hdr.byte3 == hsms.REJECT_NOT_SELECTED and hdr.system == 42


def test_t7_closes_unselected_connection():
    async def main():
        conns = []

        async def accept(r, w):
            c = hsms.HsmsConnection(r, w, active=False, t7=0.1)
            conns.append(c)
            c.start()

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        data = await asyncio.wait_for(reader.read(10), 2.0)  # EOF when passive gives up
        server.close()
        return data, conns[0].closed.is_set()

    data, closed = asyncio.run(main())
    assert data == b"" and closed
