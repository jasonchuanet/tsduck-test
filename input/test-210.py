#!/usr/bin/env python3
#-----------------------------------------------------------------------------
#
# TSDuck - The MPEG Transport Stream Toolkit
# Copyright (c) 2026, Jason Chua
# BSD-2-Clause license, see LICENSE.txt file or https://tsduck.io/license
#
#-----------------------------------------------------------------------------
#
# End-to-end test using a fixed capture from an external FFmpeg Pro-MPEG sender.
# FFmpeg produces all parity; this script only captures, drops and replays it.
# No FFmpeg source is used. FFmpeg is only needed to regenerate the fixture.
#
#-----------------------------------------------------------------------------

import argparse
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import time


def listeners():
    """Reserve a local even media port and its two associated FEC ports."""
    for _ in range(100):
        sockets = []
        reserved = False
        try:
            media = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(media)
            media.bind(("127.0.0.1", 0))
            port = media.getsockname()[1]
            if port % 2 or port > 65531:
                continue
            for offset in (2, 4):
                fec = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sockets.append(fec)
                fec.bind(("127.0.0.1", port + offset))
            for sock in sockets:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            reserved = True
            return port, sockets
        except OSError:
            pass
        finally:
            if not reserved:
                for sock in sockets:
                    sock.close()
    raise RuntimeError("Cannot reserve media/FEC UDP ports")


def capture(ffmpeg):
    """Generate a transport stream and capture the sender's real FEC."""
    port, sockets = listeners()
    # FFmpeg is run as a separate program solely to verify interoperability.
    # Pace the source so this test does not depend on kernel burst buffering.
    command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-re",
               "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25", "-t", "4",
               "-c:v", "mpeg2video", "-b:v", "500k", "-muxrate", "2M",
               "-f", "rtp_mpegts", "-fec", "prompeg=l=4:d=4",
               f"rtp://127.0.0.1:{port}?pkt_size=1328"]
    sender = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    records = []
    deadline = time.monotonic() + 15
    quiet = None
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select(sockets, [], [], 0.1)
            for sock in readable:
                data = sock.recv(65536)
                records.append((sockets.index(sock), data))
                quiet = None
            if sender.poll() is not None and not readable:
                quiet = quiet or time.monotonic()
                if time.monotonic() - quiet > 0.2:
                    break
        else:
            raise RuntimeError("FFmpeg capture timed out")
        error = sender.communicate(timeout=2)[1].decode(errors="replace")
        if sender.returncode:
            raise RuntimeError(f"FFmpeg failed: {error}")
    finally:
        if sender.poll() is None:
            sender.kill()
            sender.communicate()
        for sock in sockets:
            sock.close()
    for stream in (0, 1, 2):
        if not any(role == stream for role, _ in records):
            raise RuntimeError(f"FFmpeg did not produce stream {stream}; prompeg support is required")
    return records


def sequence(data):
    return struct.unpack_from("!H", data, 2)[0]


def load_fixture(path):
    """Read UDP payload records; ports, addresses and receive times are not stored."""
    data = path.read_bytes()
    magic = b"TSDUCK-FEC-1\n"
    if not data.startswith(magic):
        raise RuntimeError("Invalid FEC capture signature")
    records = []
    offset = len(magic)
    while offset < len(data):
        if offset + 3 > len(data):
            raise RuntimeError("Truncated FEC capture record header")
        role, length = struct.unpack_from("!BH", data, offset)
        offset += 3
        if role > 2 or offset + length > len(data):
            raise RuntimeError("Truncated or invalid FEC capture")
        records.append((role, data[offset:offset + length]))
        offset += length
    return records


def save_fixture(path, records):
    """Keep regeneration separate from normal tests and reference initialization."""
    data = bytearray(b"TSDUCK-FEC-1\n")
    for role, packet in records:
        data.extend(struct.pack("!BH", role, len(packet)))
        data.extend(packet)
    path.write_bytes(data)


def matrix(records):
    """Find a complete interior 4x4 matrix using the sender's wire headers."""
    media = [data for role, data in records if role == 0]
    positions = {sequence(data): index for index, data in enumerate(media)}
    columns = set()
    rows = set()
    for role, data in records:
        if role and len(data) >= 28:
            base = struct.unpack_from("!H", data, 12)[0]
            if role == 1 and data[25:27] == bytes((4, 4)):
                columns.add(base)
            if role == 2 and data[25:27] == bytes((1, 4)):
                rows.add(base)
    for base in rows:
        index = positions.get(base, 0)
        if 64 <= index < len(media) - 64:
            if all((base + offset) % 65536 in columns for offset in range(4)) and \
               all((base + offset) % 65536 in rows for offset in (0, 4, 8, 12)):
                return media, base
    raise RuntimeError("No complete interior FFmpeg FEC matrix was captured")


def replay(tsp, records, media, base, name, fec, loss, recover, directory,
           multicast=False, latency=200, buffer_size=4096, raw=False,
           missing_parity=False, corrupt_parity=False, source_filter=False):
    """Drop selected media packets and compare the resulting TS byte for byte."""
    port, reservations = listeners()
    dropped = {(base + offset) % 65536 for offset in loss}
    expected = b"".join(data[12:] for data in media
                        if recover or sequence(data) not in dropped)
    path = directory / f"{name}.ts"
    group = "239.255.42.42" if multicast else "127.0.0.1"
    destination = f"{group}:{port}" if multicast else str(port)
    # 'until' ends cleanly as soon as the exact expected output has arrived.
    # A receive timeout catches regressions that leave packets missing or hang.
    # Start processing after one datagram, and leave enough socket space for
    # this short replay even on a busy test host.
    command = [tsp, "-v", "--initial-input-packets", "7", "-I", "ip", destination,
               "--buffer-size", "1048576", "--receive-timeout", "2000"]
    if multicast:
        command += ["--local-address", "127.0.0.1"]
    if fec:
        command += [f"--fec={fec}", "--fec-latency", str(latency), "--fec-buffer-size", str(buffer_size)]
    command += ["-P", "until", "--packets", str(len(expected) // 188), "-O", "file", str(path)]
    # Use different parity source ports, as real FFmpeg senders do. An explicit
    # media port filter must still allow parity from the same source IP address.
    senders = []
    for _ in range(3):
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.bind(("127.0.0.1", 0))
        if multicast:
            sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        senders.append(sender)
    if source_filter:
        index = command.index("-P")
        command[index:index] = ["--source", f"127.0.0.1:{senders[0].getsockname()[1]}"]
    # Keep target ports reserved while source ports are chosen to prevent overlap.
    for sock in reservations:
        sock.close()
    receiver = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        # Allow tsp to bind its sockets before replay. Startup errors are checked.
        time.sleep(1)
        if receiver.poll() is not None:
            raise RuntimeError(receiver.communicate()[1].decode(errors="replace"))
        for role, data in records:
            if role == 0 and sequence(data) in dropped:
                continue
            if role > fec:
                continue
            # Remove or corrupt the column equation protecting the selected loss.
            if role == 1 and struct.unpack_from("!H", data, 12)[0] == base:
                if missing_parity:
                    continue
                if corrupt_parity:
                    data = data[:24] + bytes((data[24] | 0x08,)) + data[25:]
            if raw:
                data = data[12:]
            senders[role].sendto(data, (group, port + 2 * role))
            time.sleep(0.001)
        error = receiver.communicate(timeout=8)[1].decode(errors="replace")
    finally:
        for sender in senders:
            sender.close()
        if receiver.poll() is None:
            receiver.kill()
            receiver.communicate()
    actual = path.read_bytes() if path.exists() else b""
    if receiver.returncode or actual != expected:
        raise RuntimeError(f"{name}: expected {len(expected)} bytes, got {len(actual)}\n{error}")
    if fec and recover and loss and "FEC recovered 0 RTP" in error:
        raise RuntimeError(f"{name}: no FEC recovery was reported\n{error}")
    print(f"PASS {name}: {len(actual) // 188} TS packets, dropped {len(dropped)} RTP datagrams")


def idle_timeout(tsp):
    """No traffic on any socket must still allow timeout and clean worker shutdown."""
    port, reservations = listeners()
    for sock in reservations:
        sock.close()
    command = [tsp, "-I", "ip", str(port), "--fec", "--receive-timeout", "1000", "-O", "drop"]
    start = time.monotonic()
    receiver = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        error = receiver.communicate(timeout=5)[1].decode(errors="replace")
        if receiver.returncode not in (0, 1):
            raise RuntimeError(f"Idle input terminated abnormally: {error}")
        if time.monotonic() - start < 0.5:
            raise RuntimeError(f"Idle input failed before its timeout: {error}")
    finally:
        if receiver.poll() is None:
            receiver.kill()
            receiver.communicate()
    print("PASS idle-timeout: media and parity workers stopped")


def main():
    parser = argparse.ArgumentParser(description="SMPTE 2022-1 IP input regression test")
    parser.add_argument("--tsp", default="tsp", help="Path to the tsp executable under test")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_suffix(".rtp"))
    parser.add_argument("--capture", action="store_true", help="Regenerate the fixture using FFmpeg and exit")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg with the prompeg protocol")
    args = parser.parse_args()
    # Selecting a build directory also selects its matching shared libraries and plugins.
    if os.path.dirname(args.tsp):
        bindir = str(Path(args.tsp).resolve().parent)
        os.environ["TSPLUGINS_PATH"] = bindir
        os.environ["LD_LIBRARY_PATH"] = bindir + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    if args.capture:
        save_fixture(args.fixture, capture(args.ffmpeg))
        return
    records = load_fixture(args.fixture)
    media, base = matrix(records)
    with tempfile.TemporaryDirectory(prefix="tsduck-ip-fec-") as directory:
        path = Path(directory)
        replay(args.tsp, records, media, base, "raw-udp-without-fec", 0, (), True, path, raw=True)
        replay(args.tsp, records, media, base, "rtp-without-fec", 0, (), True, path)
        replay(args.tsp, records, media, base, "column-burst", 1, (4, 5, 6, 7), True, path)
        replay(args.tsp, records, media, base, "iterative-2d", 2, (1, 5, 6), True, path)
        replay(args.tsp, records, media, base, "unrecoverable-rectangle", 2, (5, 6, 9, 10), False, path)
        replay(args.tsp, records, media, base, "lost-column-parity", 1, (4,), False, path, missing_parity=True)
        replay(args.tsp, records, media, base, "malformed-column-parity", 1, (4,), False, path, corrupt_parity=True)
        replay(args.tsp, records, media, base, "sequence-window", 1, (), True, path, latency=1000, buffer_size=256)
        replay(args.tsp, records, media, base, "source-filter-2d", 2, (1, 5, 6), True, path, source_filter=True)
        replay(args.tsp, records, media, base, "multicast-2d", 2, (1, 5, 6), True, path, multicast=True)
    idle_timeout(args.tsp)


if __name__ == "__main__":
    main()
