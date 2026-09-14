#!/usr/bin/env python3
"""Inject RAD EDOPro replay-timeline control packets into .yrpX files."""
from __future__ import annotations

import argparse
import lzma
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

REPLAY_COMPRESSED = 0x1
REPLAY_SINGLE_MODE = 0x8
REPLAY_NEWREPLAY = 0x20
REPLAY_64BIT_DUELFLAG = 0x100
REPLAY_EXTENDED_HEADER = 0x200
REPLAY_YRPX = 0x58707279

RAD_MESSAGE_ID = 232
RAD_VERSION = 1
OP_PACE = 1
OP_WAIT = 2
OP_RESET = 3
OP_PAUSE = 4
MIN_PACE = 50
MAX_PACE = 150
MAX_WAIT_MS = 10 * 60 * 1000

MSG_NAMES = {
    1: "RETRY", 2: "HINT", 4: "START", 5: "WIN", 6: "UPDATE_DATA",
    7: "UPDATE_CARD", 30: "CONFIRM_DECKTOP", 31: "CONFIRM_CARDS",
    32: "SHUFFLE_DECK", 33: "SHUFFLE_HAND", 40: "NEW_TURN", 41: "NEW_PHASE",
    50: "MOVE", 53: "POS_CHANGE", 54: "SET", 55: "SWAP", 60: "SUMMONING",
    61: "SUMMONED", 62: "SPSUMMONING", 63: "SPSUMMONED", 64: "FLIPSUMMONING",
    65: "FLIPSUMMONED", 70: "CHAINING", 71: "CHAINED", 72: "CHAIN_SOLVING",
    73: "CHAIN_SOLVED", 74: "CHAIN_END", 90: "BECOME_TARGET", 92: "DAMAGE",
    93: "RECOVER", 100: "PAY_LPCOST", 110: "ATTACK", 111: "BATTLE",
    130: "TOSS_COIN", 131: "TOSS_DICE", 160: "CARD_HINT", 161: "TAG_SWAP",
    162: "RELOAD_FIELD", 163: "AI_NAME", 164: "SHOW_HINT", 165: "PLAYER_HINT",
    170: "MATCH_KILL", 180: "CUSTOM_MSG", 190: "REMOVE_CARDS", 231: "OLD_REPLAY_MODE",
}

@dataclass
class Packet:
    message: int
    data: bytes

@dataclass
class ReplayFile:
    header: bytearray
    payload_prefix: bytes
    packets: list[Packet]
    compressed: bool
    filters: list[dict] | None


def _decode_lzma_props(props: bytes) -> list[dict]:
    if len(props) < 5:
        raise ValueError("LZMA properties are missing")
    prop0 = props[0]
    if prop0 >= 9 * 5 * 5:
        raise ValueError("Unsupported LZMA property byte")
    lc = prop0 % 9
    rem = prop0 // 9
    lp = rem % 5
    pb = rem // 5
    dict_size = int.from_bytes(props[1:5], "little") or (1 << 23)
    return [{"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}]


def _header_size(flag: int) -> int:
    return 72 if (flag & REPLAY_EXTENDED_HEADER) else 32


def _payload_stream_offset(payload: bytes, flag: int) -> int:
    pos = 0
    if flag & REPLAY_SINGLE_MODE:
        pos += 80
        if pos > len(payload):
            raise ValueError("Replay payload is truncated in player names")
    else:
        if flag & REPLAY_NEWREPLAY:
            if pos + 4 > len(payload):
                raise ValueError("Replay payload is truncated before home player count")
            home = struct.unpack_from("<I", payload, pos)[0]
            pos += 4
        else:
            home = 1
        pos += home * 40
        if pos > len(payload):
            raise ValueError("Replay payload is truncated in home player names")
        if flag & REPLAY_NEWREPLAY:
            if pos + 4 > len(payload):
                raise ValueError("Replay payload is truncated before opposing player count")
            opposing = struct.unpack_from("<I", payload, pos)[0]
            pos += 4
        else:
            opposing = 1
        pos += opposing * 40
        if pos > len(payload):
            raise ValueError("Replay payload is truncated in opposing player names")
    pos += 8 if (flag & REPLAY_64BIT_DUELFLAG) else 4
    if pos > len(payload):
        raise ValueError("Replay payload is truncated in duel parameters")
    return pos


def _parse_packets(stream: bytes) -> list[Packet]:
    packets: list[Packet] = []
    pos = 0
    while pos < len(stream):
        if pos + 5 > len(stream):
            raise ValueError(f"Truncated packet header at stream offset {pos}")
        message = stream[pos]
        length = struct.unpack_from("<I", stream, pos + 1)[0]
        pos += 5
        end = pos + length
        if end > len(stream):
            raise ValueError(f"Truncated packet payload at stream offset {pos}")
        packets.append(Packet(message, stream[pos:end]))
        pos = end
    return packets


def read_replay(path: Path) -> ReplayFile:
    raw = path.read_bytes()
    if len(raw) < 32:
        raise ValueError("File is too small to be a replay")
    replay_id, _version, flag, _timestamp, datasize = struct.unpack_from("<IIIII", raw, 0)
    if replay_id != REPLAY_YRPX:
        raise ValueError("Only streamed .yrpX replays are supported")
    hsize = _header_size(flag)
    if len(raw) < hsize:
        raise ValueError("Replay header is truncated")
    header = bytearray(raw[:hsize])
    body = raw[hsize:]
    filters = None
    if flag & REPLAY_COMPRESSED:
        filters = _decode_lzma_props(bytes(header[24:29]))
        payload = lzma.decompress(body, format=lzma.FORMAT_RAW, filters=filters)
        if datasize and len(payload) != datasize:
            raise ValueError(f"Uncompressed size mismatch: header={datasize}, actual={len(payload)}")
    else:
        payload = body
    stream_offset = _payload_stream_offset(payload, flag)
    return ReplayFile(header, payload[:stream_offset], _parse_packets(payload[stream_offset:]), bool(flag & REPLAY_COMPRESSED), filters)


def write_replay(replay: ReplayFile, path: Path) -> None:
    stream = bytearray()
    for packet in replay.packets:
        stream.append(packet.message & 0xFF)
        stream += struct.pack("<I", len(packet.data))
        stream += packet.data
    payload = replay.payload_prefix + bytes(stream)
    struct.pack_into("<I", replay.header, 16, len(payload))
    if replay.compressed:
        assert replay.filters is not None
        body = lzma.compress(payload, format=lzma.FORMAT_RAW, filters=replay.filters)
    else:
        body = payload
    path.write_bytes(bytes(replay.header) + body)


def make_control(op: int, value: int = 0) -> Packet:
    return Packet(RAD_MESSAGE_ID, struct.pack("<BBI", RAD_VERSION, op, value))


def is_control(packet: Packet) -> bool:
    return packet.message == RAD_MESSAGE_ID and len(packet.data) == 6 and packet.data[0] == RAD_VERSION


def describe_control(packet: Packet) -> str:
    if not is_control(packet):
        return "RAD?"
    _version, op, value = struct.unpack("<BBI", packet.data)
    return {OP_PACE: f"PACE {value}", OP_WAIT: f"WAIT {value}ms", OP_RESET: "RESET", OP_PAUSE: "PAUSE"}.get(op, f"OP{op} {value}")


def parse_timeline(path: Path, packet_count: int):
    before: dict[int, list[Packet]] = {}
    after: dict[int, list[Packet]] = {}
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"{path}:{lineno}: expected: before|after INDEX COMMAND [VALUE]")
        where = parts[0].lower()
        if where not in ("before", "after"):
            raise ValueError(f"{path}:{lineno}: first token must be before or after")
        try:
            index = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: invalid packet index") from exc
        command = parts[2].lower()
        value = None
        if len(parts) >= 4:
            try:
                value = int(parts[3])
            except ValueError as exc:
                raise ValueError(f"{path}:{lineno}: invalid numeric value") from exc
        if command == "pace":
            if value is None or not (MIN_PACE <= value <= MAX_PACE):
                raise ValueError(f"{path}:{lineno}: PACE must be {MIN_PACE}..{MAX_PACE}")
            packet = make_control(OP_PACE, value)
        elif command == "wait":
            if value is None or not (0 <= value <= MAX_WAIT_MS):
                raise ValueError(f"{path}:{lineno}: WAIT must be 0..{MAX_WAIT_MS} ms")
            packet = make_control(OP_WAIT, value)
        elif command == "reset":
            if value is not None:
                raise ValueError(f"{path}:{lineno}: RESET takes no value")
            packet = make_control(OP_RESET)
        elif command == "pause":
            if value is not None:
                raise ValueError(f"{path}:{lineno}: PAUSE takes no value")
            packet = make_control(OP_PAUSE)
        else:
            raise ValueError(f"{path}:{lineno}: unknown command {command!r}")
        if where == "before":
            if not (0 <= index <= packet_count):
                raise ValueError(f"{path}:{lineno}: before index must be 0..{packet_count}")
            before.setdefault(index, []).append(packet)
        else:
            if not (0 <= index < packet_count):
                raise ValueError(f"{path}:{lineno}: after index must be 0..{packet_count - 1}")
            after.setdefault(index, []).append(packet)
    return before, after


def strip_controls(packets: list[Packet]) -> list[Packet]:
    return [packet for packet in packets if not is_control(packet)]


def cmd_inspect(args) -> None:
    replay = read_replay(Path(args.input))
    normal_index = 0
    for physical_index, packet in enumerate(replay.packets):
        if is_control(packet):
            print(f"RAD\t{physical_index}\t{describe_control(packet)}")
            continue
        name = MSG_NAMES.get(packet.message, "")
        print(f"{normal_index}\tmsg={packet.message}\tlen={len(packet.data)}\t{name}")
        normal_index += 1
    print(f"# non-RAD packets: {normal_index}")


def cmd_strip(args) -> None:
    replay = read_replay(Path(args.input))
    old = len(replay.packets)
    replay.packets = strip_controls(replay.packets)
    write_replay(replay, Path(args.output))
    print(f"Removed {old - len(replay.packets)} RAD timeline packet(s).")


def cmd_apply(args) -> None:
    replay = read_replay(Path(args.input))
    base_packets = strip_controls(replay.packets)
    before, after = parse_timeline(Path(args.timeline), len(base_packets))
    out: list[Packet] = []
    for i, packet in enumerate(base_packets):
        out.extend(before.get(i, ()))
        out.append(packet)
        out.extend(after.get(i, ()))
    out.extend(before.get(len(base_packets), ()))
    replay.packets = out
    write_replay(replay, Path(args.output))
    controls = sum(1 for packet in out if is_control(packet))
    print(f"Wrote {args.output} with {controls} RAD timeline command(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAD EDOPro .yrpX replay timeline editor")
    sub = parser.add_subparsers(dest="cmd", required=True)
    item = sub.add_parser("inspect", help="list packet indexes and existing timeline commands")
    item.add_argument("input")
    item.set_defaults(func=cmd_inspect)
    item = sub.add_parser("apply", help="replace timeline commands using a text timeline file")
    item.add_argument("input")
    item.add_argument("timeline")
    item.add_argument("output")
    item.set_defaults(func=cmd_apply)
    item = sub.add_parser("strip", help="remove all RAD timeline commands")
    item.add_argument("input")
    item.add_argument("output")
    item.set_defaults(func=cmd_strip)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except (OSError, ValueError, lzma.LZMAError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
