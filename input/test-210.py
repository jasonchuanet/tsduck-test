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
from contextlib import ExitStack
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
    # RTP media uses an even destination port in this profile.
    # Choosing an ephemeral port lets independent test jobs run concurrently.
    # The kernel does not reserve neighboring ports when assigning the first one.
    # Reserve +2 and +4 as a group before a sender or receiver process starts.
    for _ in range(100):
        sockets = []
        reserved = False
        try:
            media = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(media)
            # A numeric IPv4 loopback address avoids platform-dependent DNS results.
            # Localhost may resolve to IPv6, while this fixture test explicitly uses IPv4.
            media.bind(("127.0.0.1", 0))
            port = media.getsockname()[1]
            # Reject odd ports and values whose row port would overflow UDP's range.
            # The finally block also closes this socket when the loop continues.
            if port % 2 or port > 65531:
                continue
            for offset in (2, 4):
                fec = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sockets.append(fec)
                fec.bind(("127.0.0.1", port + offset))
            # Capture and replay are finite bursts; buffering prevents the capture
            # from depending on the operating system's small default receive queue.
            for sock in sockets:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            # Transfer ownership only after all three port reservations succeeded.
            # Failed attempts are cleaned up below, including partially bound groups.
            reserved = True
            return port, sockets
        # A neighboring port can already be used even if the media bind succeeded.
        # Retry another group instead of making that ordinary conflict a test failure.
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
    # Guard all bound sockets before any fallible process or source setup.
    with ExitStack() as resources:
        for sock in sockets:
            resources.callback(sock.close)
        # FFmpeg is run as a separate program solely to verify interoperability.
        # Pace the source so this test does not depend on kernel burst buffering.
        command = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-re",
                   "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=25", "-t", "4",
                   "-c:v", "mpeg2video", "-b:v", "500k", "-muxrate", "2M",
                   "-f", "rtp_mpegts", "-fec", "prompeg=l=4:d=4",
                   f"rtp://127.0.0.1:{port}?pkt_size=1328"]
        # Disable terminal input and progress output so background execution is stable.
        # Error output is retained only to explain a failed fixture regeneration.
        # The sender is an external program, not linked into the BSD test harness.
        sender = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        records = []
        # Monotonic time makes wall-clock changes irrelevant to capture completion.
        # This outer limit also catches a sender that never exits after its four seconds.
        deadline = time.monotonic() + 15
        quiet = None
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select(sockets, [], [], 0.1)
                # All three sockets are serviced together; media-only reads would lose
                # the independently queued column and row parity needed by later tests.
                for sock in readable:
                    data = sock.recv(65536)
                    records.append((sockets.index(sock), data))
                    quiet = None
                # A process exit can precede draining its final datagrams from the kernel.
                # Wait for a short quiet interval rather than stopping on exit alone.
                if sender.poll() is not None and not readable:
                    quiet = quiet or time.monotonic()
                    if time.monotonic() - quiet > 0.2:
                        break
            else:
                raise RuntimeError("FFmpeg capture timed out")
            # Always reap the child and inspect its exit status before accepting a capture.
            # A file produced by a failed encoder must not silently become a new fixture.
            error = sender.communicate(timeout=2)[1].decode(errors="replace")
            if sender.returncode:
                raise RuntimeError(f"FFmpeg failed: {error}")
        finally:
            if sender.poll() is None:
                sender.kill()
                sender.communicate()
            for sock in sockets:
                sock.close()
        # Regeneration is useful only if the installed FFmpeg supports the Pro-MPEG sender.
        # Requiring every stream catches a disabled or incorrectly specified FEC protocol.
        for stream in (0, 1, 2):
            if not any(role == stream for role, _ in records):
                raise RuntimeError(f"FFmpeg did not produce stream {stream}; prompeg support is required")
        return records


def sequence(data):
    # The RTP header was length-checked by the fixture reader before this accessor.
    return struct.unpack_from("!H", data, 2)[0]


def load_fixture(path):
    """Read UDP payload records; ports, addresses and receive times are not stored."""
    data = path.read_bytes()
    magic = b"TSDUCK-FEC-1\n"
    # This simple container stores UDP payloads, not Ethernet or IP headers.
    # It therefore needs no packet-capture library, device access or privileges.
    # A signature prevents another binary input from being interpreted as lengths.
    if not data.startswith(magic):
        raise RuntimeError("Invalid FEC capture signature")
    records = []
    offset = len(magic)
    while offset < len(data):
        # The record prefix uses network byte order and a bounded 16-bit length.
        # Check the prefix before unpacking, and the payload before any wire fields.
        if offset + 3 > len(data):
            raise RuntimeError("Truncated FEC capture record header")
        # Big-endian storage makes a fixture identical on every target architecture.
        role, length = struct.unpack_from("!BH", data, offset)
        offset += 3
        if role > 2 or offset + length > len(data):
            raise RuntimeError("Truncated or invalid FEC capture")
        # Enforce the fixture's RTP profile before matrix selection or loss injection.
        # Real malformed-parity tests modify validated packets later during replay.
        packet = data[offset:offset + length]
        minimum = 12 if role == 0 else 28
        if length <= minimum or length > minimum + 7 * 204:
            raise RuntimeError("Invalid captured RTP datagram length")
        # The reference comparison counts 188-byte packets from the FFmpeg sender.
        # RS204 coverage belongs to the independent decoder unit tests.
        if role == 0 and (length - 12) % 188 != 0:
            raise RuntimeError("Capture media must contain complete 188-byte TS packets")
        records.append((role, packet))
        # Advance by the validated record length, never by a guessed RTP payload size.
        offset += length
    return records


def save_fixture(path, records):
    """Keep regeneration separate from normal tests and reference initialization."""
    # Addresses, ports and arrival clocks are deliberately absent from the fixture.
    # RTP identifiers and timestamps remain in the payload, where parity protects them.
    # Normal tests never write this file; regeneration requires an explicit flag.
    data = bytearray(b"TSDUCK-FEC-1\n")
    for role, packet in records:
        data.extend(struct.pack("!BH", role, len(packet)))
        data.extend(packet)
    # Regeneration writes the complete container only after all streams were verified.
    path.write_bytes(data)


def matrix(records):
    """Find a complete interior 4x4 matrix using the sender's wire headers."""
    media = [data for role, data in records if role == 0]
    # Media order is canonical on its single UDP socket, independently of parity order.
    # Sequence numbers associate groups without trusting the parity RTP sequence.
    positions = {sequence(data): index for index, data in enumerate(media)}
    # Distinct bases avoid mistaking duplicate parity packets for additional protection.
    columns = set()
    rows = set()
    # Read SNBase, Offset and NA from the sender's actual FEC header.
    # Do not duplicate the decoder's parity arithmetic inside this interoperability test.
    # A 4x4 block is selected only when all four rows and columns are present.
    for role, data in records:
        if role and len(data) >= 28:
            base = struct.unpack_from("!H", data, 12)[0]
            if role == 1 and data[25:27] == bytes((4, 4)):
                columns.add(base)
            if role == 2 and data[25:27] == bytes((1, 4)):
                rows.add(base)
    # Sorting makes the choice deterministic across Python versions and set layouts.
    # Leave margins at both ends so selected losses are neither startup nor tail cases.
    # Leading loss, reordering and wrap are exercised separately with a deterministic clock.
    for base in sorted(rows):
        index = positions.get(base, 0)
        if 64 <= index < len(media) - 64:
            if all((base + offset) % 65536 in columns for offset in range(4)) and \
               all((base + offset) % 65536 in rows for offset in (0, 4, 8, 12)):
                return media, base
    raise RuntimeError("No complete interior FFmpeg FEC matrix was captured")


def make_command(tsp, destination, path, packets, fec, multicast, latency, buffer_size, source_port):
    """Keep receiver setup independent of packet loss and wire mutation."""
    # Small preloading starts the pipeline while this finite fixture is still arriving.
    # A large socket buffer prevents unrelated host scheduling from becoming test loss.
    command = [tsp, "-v", "--initial-input-packets", "7", "-I", "ip", destination,
               "--buffer-size", "1048576", "--receive-timeout", "2000"]
    if multicast:
        # Specify loopback on both endpoints instead of relying on the default route.
        command += ["--local-address", "127.0.0.1"]
    if fec:
        # One flag discovers both column-only and row/column senders, like dektec input.
        command += ["--smpte-2022-fec", "--smpte-2022-fec-latency", str(latency), "--smpte-2022-fec-buffer-size", str(buffer_size)]
    if source_port is not None:
        # Parity comes from other ports; filtering it by this port would break recovery.
        command += ["--source", f"127.0.0.1:{source_port}"]
    # End at the exact expected TS length; the timeout catches incomplete recovery.
    command += ["-P", "until", "--packets", str(packets), "-O", "file", str(path)]
    # Global processing limits precede -I; media source filtering belongs to the input.
    return command


def open_senders(multicast):
    """Allocate distinct source ports while the destination ports are reserved."""
    senders = []
    try:
        for _ in range(3):
            # One socket per role reproduces FFmpeg's independent parity source ports.
            sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            senders.append(sender)
            sender.bind(("127.0.0.1", 0))
            if multicast:
                # Loopback membership requires the loopback interface on the sender too.
                sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        # Ownership transfers to the replay guard only after every source bind succeeds.
        return senders
    except OSError:
        # A partial bind failure must not leave sockets around for the next case.
        for sender in senders:
            sender.close()
        raise


def send_capture(senders, records, group, port, fec, dropped, base,
                 missing_parity, corrupt_parity, raw):
    """Replay independently generated parity without recomputing any equation."""
    # Loss injection happens before sending, so recovery cannot use a hidden original.
    # Parity is never regenerated from the selected survivors by this harness.
    for role, data in records:
        if role == 0 and sequence(data) in dropped:
            continue
        # Column-only mode must work without any row packets being transmitted.
        # The direct-path cases send media alone, even though the fixture includes FEC.
        # Mode selects which wire streams exist; decoder acceptance is tested separately.
        if role > fec:
            continue
        # Remove or corrupt the column equation protecting the selected loss.
        if role == 1 and struct.unpack_from("!H", data, 12)[0] == base:
            if missing_parity:
                continue
            if corrupt_parity:
                data = data[:24] + bytes((data[24] | 0x08,)) + data[25:]
        # Stripping only the media RTP header checks the existing raw UDP path.
        # FEC is disabled in this case; parity must never be mistaken for TS input.
        # Preserve captured RTP in every FEC case, since it carries recovery sequence and time.
        if raw:
            data = data[12:]
        # Destination ports follow the standard's media/+2/+4 convention.
        # Fixed pacing keeps the fixture independent of kernel burst-queue defaults.
        senders[role].sendto(data, (group, port + 2 * role))
        time.sleep(0.001)


def check_output(returncode, path, expected, fec, recover, dropped, name, error):
    """Validate process completion and exact media, then print a stable summary."""
    # Successful exit is necessary but not sufficient: compare the entire media output.
    # Duplicate output, wrong ordering or incorrectly reconstructed bytes must all fail.
    actual = path.read_bytes() if path.exists() else b""
    if returncode or actual != expected:
        raise RuntimeError(f"{name}: expected {len(expected)} bytes, got {len(actual)}\n{error}")
    # The independent byte comparison proves recovery; also check the reported statistics.
    # Logging is kept out of the reference except for stable, case-level summaries.
    if fec and recover and dropped and "FEC recovered 0 RTP" in error:
        raise RuntimeError(f"{name}: no FEC recovery was reported\n{error}")
    # Do not include ephemeral ports, random RTP identifiers or receive timestamps.
    # These summaries are the portable reference output used by the shell framework.
    print(f"PASS {name}: {len(actual) // 188} TS packets, dropped {len(dropped)} RTP datagrams")


# Allow host scheduling pauses without expiring intentionally recoverable losses.
# Precise deadline behavior is checked separately with the decoder's simulated clock.
def replay(tsp, records, media, base, name, fec, loss, recover, directory,
           multicast=False, latency=1000, buffer_size=4096, raw=False,
           missing_parity=False, corrupt_parity=False, source_filter=False):
    """Drop selected media packets and compare the resulting TS byte for byte."""
    port, reservations = listeners()
    # Guard all bound sockets before any fallible process or source setup.
    with ExitStack() as resources:
        for sock in reservations:
            resources.callback(sock.close)
        dropped = {(base + offset) % 65536 for offset in loss}
        # Recoverable cases must retain every original TS byte, including PCR and stuffing.
        # Unrecoverable cases omit exactly the intentionally dropped datagrams.
        # This is stronger than comparing only packet counts or continuity counters.
        expected = b"".join(data[12:] for data in media
                            if recover or sequence(data) not in dropped)
        path = directory / f"{name}.ts"
        group = "239.255.42.42" if multicast else "127.0.0.1"
        destination = f"{group}:{port}" if multicast else str(port)
        # Choose source ports before releasing the target reservations.
        # Otherwise a sender could claim a parity destination and hide a setup error.
        # The source-filter case deliberately uses the media sender's full IP:port.
        senders = open_senders(multicast)
        for sender in senders:
            resources.callback(sender.close)
        command = make_command(tsp, destination, path, len(expected) // 188, fec, multicast,
                               latency, buffer_size, senders[0].getsockname()[1] if source_filter else None)
        # Target ports stay reserved until every source port has been selected.
        for sock in reservations:
            sock.close()
        # The child writes media to a temporary file, while stderr carries diagnostics.
        # Quiet verbose output is small; per-datagram debug output could fill that pipe.
        receiver = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            # Allow process scheduling and socket setup on busy hosts before the finite replay.
            # Check early termination so command-line and bind errors are not misreported as loss.
            time.sleep(1)
            if receiver.poll() is not None:
                raise RuntimeError(receiver.communicate()[1].decode(errors="replace"))
            # All parity stays exactly as emitted by FFmpeg, except intentional fault injection.
            # The receive timeout and communicate timeout bound missing output and shutdown.
            send_capture(senders, records, group, port, fec, dropped, base,
                         missing_parity, corrupt_parity, raw)
            error = receiver.communicate(timeout=8)[1].decode(errors="replace")
        finally:
            # Stop and reap the child before ExitStack releases the bound sender sockets.
            # This also handles exceptions from replay, comparison or timeout.
            if receiver.poll() is None:
                receiver.kill()
                receiver.communicate()
        check_output(receiver.returncode, path, expected, fec, recover, dropped, name, error)


def idle_timeout(tsp):
    """No traffic on any socket must still allow timeout and clean worker shutdown."""
    port, reservations = listeners()
    for sock in reservations:
        sock.close()
    command = [tsp, "-I", "ip", str(port), "--smpte-2022-fec", "--receive-timeout", "1000", "-O", "drop"]
    # No media or parity arrives: only media inactivity should stop the input.
    # Reaping the process proves that idle parity receivers do not prevent shutdown.
    # The elapsed-time lower bound distinguishes timeout from an immediate setup failure.
    start = time.monotonic()
    receiver = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        error = receiver.communicate(timeout=5)[1].decode(errors="replace")
        # TSDuck may report the receive timeout as an ordinary error exit.
        # Signal termination and other abnormal exits must not count as a successful test.
        if receiver.returncode not in (0, 1):
            raise RuntimeError(f"Idle input terminated abnormally: {error}")
        # Wall-clock timestamps are not compared or written to the reference.
        # A monotonic elapsed interval is used only to reject immediate setup errors.
        if time.monotonic() - start < 0.5:
            raise RuntimeError(f"Idle input failed before its timeout: {error}")
    finally:
        if receiver.poll() is None:
            receiver.kill()
            receiver.communicate()
    # Process completion also proves the receiver workers have released their resources.
    # The reference contains the result, not the variable amount of host scheduling time.
    # The shell framework compares this fixed result across all supported platforms.
    print("PASS idle-timeout: media and parity workers stopped")


def input_options(tsp):
    """Verify the dektec-compatible receive flag and its dependent controls."""
    # Input needs no requested dimension: every equation already describes its members.
    # An output-style value must fail during parsing instead of becoming a port parameter.
    cases = [(["--smpte-2022-fec=2d"], None),
             (["--smpte-2022-fec-latency", "1000"], "require --smpte-2022-fec"),
             (["--smpte-2022-fec-buffer-size", "4096"], "require --smpte-2022-fec"),
             (["--fec=2"], None)]
    for options, message in cases:
        # Bound an accidentally accepted configuration with media inactivity timeout.
        # Matching the validation message excludes unrelated socket or timeout failures.
        result = subprocess.run([tsp, "-I", "ip", "5000", "--receive-timeout", "100", *options, "-O", "drop"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
        # Parser diagnostics are checked directly in RTPFECOptionsTest, using
        # a report buffer before any process exit. The CLI still must reject
        # these arguments; FEC-specific loadArgs errors also require their text.
        if result.returncode != 1 or (message is not None and message not in result.stderr.decode(errors="replace")):
            raise RuntimeError(f"Incorrect input FEC option validation: {options}")
    # The normal recovery cases separately prove the flag works for both wire dimensions.
    print("PASS input-options: receive flag and dependent controls validated")


def main():
    parser = argparse.ArgumentParser(description="SMPTE 2022-1 IP input regression test")
    parser.add_argument("--tsp", default="tsp", help="Path to the tsp executable under test")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).with_suffix(".rtp"))
    parser.add_argument("--capture", action="store_true", help="Regenerate the fixture using FFmpeg and exit")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg with the prompeg protocol")
    args = parser.parse_args()
    # Selecting a build directory also selects its matching shared libraries and plugins.
    # A selected development executable must load its matching libraries and plugins.
    # The shell framework supplies platform-specific paths for installed and native builds.
    if os.path.dirname(args.tsp):
        bindir = str(Path(args.tsp).resolve().parent)
        os.environ["TSPLUGINS_PATH"] = bindir
        os.environ["LD_LIBRARY_PATH"] = bindir + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    # Capturing is an explicit maintenance operation, separate from reference initialization.
    # Tests run from a fixed fixture so normal CI does not depend on codecs or FFmpeg versions.
    if args.capture:
        save_fixture(args.fixture, capture(args.ffmpeg))
        return
    records = load_fixture(args.fixture)
    media, base = matrix(records)
    # Each case owns its output file and all sockets; independent jobs cannot collide.
    # The same receiver flag handles either wire dimension; replay selects the parity streams.
    # Burst and iterative patterns use complete independently captured parity groups.
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
    input_options(args.tsp)


if __name__ == "__main__":
    main()
