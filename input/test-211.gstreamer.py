#!/usr/bin/env python3
#-----------------------------------------------------------------------------
#
# TSDuck - The MPEG Transport Stream Toolkit
# Copyright (c) 2026, Jason Chua
# BSD-2-Clause license, see LICENSE.txt file or https://tsduck.io/license
#
#-----------------------------------------------------------------------------
# Optional interoperability check using GStreamer's independent ST 2022-1 receiver.
# GStreamer runs as a separate program; no implementation code is copied or linked.

import argparse
import importlib.util
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

# Reuse the normal test's capture and byte validator, never the production decoder.
# Keep optional dependencies out of test-211.sh and the mandatory regression suite.
# Resolve helpers relative to this file so invocation from another directory remains valid.
# Importing these helpers neither starts TSDuck nor loads a GStreamer Python binding.
spec = importlib.util.spec_from_file_location("fec_output_fixture", Path(__file__).with_name("test-211.py"))
fixture = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True
spec.loader.exec_module(fixture)

# The fixed 4x4 matrix permits a burst case and an iterative row/column recovery case.
# Losses occur in an interior matrix with a complete later column-emission window.
COLUMNS = ROWS = 4
BURST = 7


def pipeline(gst_launch, port, output, mode):
    """Build a receiver pipeline with separately wired media and parity destinations."""
    # Retain equations for five seconds, comfortably beyond this short loopback replay.
    # The jitter buffer delays depayloading so recovered media can fill sequence gaps.
    media_caps = "application/x-rtp,media=video,encoding-name=MP2T,clock-rate=90000,payload=33"
    parity_caps = "application/x-rtp,clock-rate=90000,payload=96"
    # -e flushes filesink on SIGINT; -q leaves only useful diagnostic messages in the log.
    # Every pipeline token is an argument, avoiding shell parsing of caps or file paths.
    command = [gst_launch, "-e", "-q", "rtpst2022-1-fecdec", "name=fec", "size-time=5000000000",
               "!", "rtpjitterbuffer", "latency=1000", "!", "rtpmp2tdepay", "!", "filesink",
               "location=" + str(output), "sync=false",
               "udpsrc", "address=127.0.0.1", "port=" + str(port), "caps=" + media_caps, "!", "fec.sink",
               "udpsrc", "address=127.0.0.1", "port=" + str(port + 2), "caps=" + parity_caps, "!", "fec.fec_0"]
    # In 1D mode there is no row receiver pad and no row stream to conceal a column error.
    # The second request pad is wired only for the two-stream case.
    if mode == "2":
        command += ["udpsrc", "address=127.0.0.1", "port=" + str(port + 4),
                    "caps=" + parity_caps, "!", "fec.fec_1"]
    return command


def schedule(records, media):
    """Reconstruct the specified emission schedule without altering captured parity."""
    # UDP queues do not preserve global cross-port arrival order in the capture utility.
    # SNBase and the independently known geometry identify each equation's emission point.
    positions = {struct.unpack_from("!H", data, 2)[0]: index for index, data in enumerate(media)}
    events = {}
    for role, data, _ in records:
        if role:
            base = positions[struct.unpack_from("!H", data, 12)[0]]
            # A row follows its last protected member; a column follows c*D in the next matrix.
            # This is the aligned Annex C schedule, independently calculated from wire SNBase.
            when = base + COLUMNS - 1 if role == 2 else \
                (base // (COLUMNS * ROWS) + 1) * COLUMNS * ROWS + (base % COLUMNS) * ROWS
            events.setdefault(when, []).append((role, data))
    return events


def replay(process, port, media, events, losses):
    """Send original wire packets from one source socket, omitting selected media."""
    # The source IP and port are common to media, column FEC and row FEC.
    # A context manager closes the sender even if the receiver or a send fails.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        sender.bind(("127.0.0.1", 0))
        # gst-launch starts asynchronously; allow UDP receiver sockets to bind before replay.
        # An early exit indicates a missing plugin or invalid pipeline, not successful recovery.
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError("GStreamer receiver exited before replay")
        for index, data in enumerate(media):
            # Drop only media: the receiver must reconstruct those exact RTP payloads from FEC.
            if index not in losses:
                sender.sendto(data, ("127.0.0.1", port))
            for role, parity in events.get(index, []):
                sender.sendto(parity, ("127.0.0.1", port + 2 * role))
            # Bound instantaneous traffic, making the test independent of local queue capacity.
            # Pacing affects wall time only; protected media timestamps are left unchanged.
            time.sleep(0.005)
        # Let the jitter buffer release the final media before requesting a clean EOS flush.
        # No synthetic media or parity is sent to make the receiver finish.
        time.sleep(2)
        process.send_signal(signal.SIGINT)
        process.wait(timeout=5)


def check_receiver(gst_launch, records, media, source, directory, mode, losses):
    """Run the independent decoder and compare all recovered TS bytes."""
    # Reserve the standard port group, then relinquish it to the external receiver.
    # GStreamer owns these sockets; the replay sender uses a different ephemeral port.
    port, reservations = fixture.fixture.listeners()
    for reservation in reservations:
        reservation.close()
    output = directory / ("recovered-" + mode + ".ts")
    log = directory / ("receiver-" + mode + ".log")
    events = schedule(records, media)
    # A regular file drains arbitrary diagnostics without a pipe filling and stalling replay.
    # Guard the child before any further operation that can fail.
    with log.open("w") as error:
        process = subprocess.Popen(pipeline(gst_launch, port, output, mode), stdout=error, stderr=subprocess.STDOUT)
        try:
            replay(process, port, media, events, losses)
        except Exception as failure:
            # Preserve the external program's explanation when startup or replay fails.
            error.flush()
            raise RuntimeError(f"{failure}\n{log.read_text()}") from failure
        finally:
            # Reap before deleting temporary files, including interruption and timeout paths.
            if process.poll() is None:
                process.kill()
                process.wait()
    # Counts alone could miss reordering or conceal corrupt reconstruction.
    # Require successful termination and exact equality with all originally transmitted TS.
    actual = output.read_bytes() if output.exists() else b""
    if process.returncode != 0 or actual != source:
        raise RuntimeError(f"GStreamer {mode}D recovery failed: {len(actual)} of {len(source)} bytes\n{log.read_text()}")
    # Report only after complete byte comparison, including packets after the loss window.
    # Temporary paths and ephemeral ports do not enter the reproducible summary.
    print(f"PASS GStreamer independent receiver {mode}D: recovered {len(losses)} lost RTP datagrams, "
          f"{len(actual) // 188} exact TS packets")


def main():
    # Both executables are explicit parameters, so the tested TSDuck branch is unambiguous.
    # This optional Unix check requires gst-launch and the rtpst2022-1-fecdec plugin.
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsp", required=True)
    parser.add_argument("--gst-launch", default="gst-launch-1.0")
    args = parser.parse_args()
    # Take only generated transport content from the existing licensed fixture.
    # Three matrices include a protected interior group and its delayed column parity.
    records = fixture.fixture.load_fixture(Path(__file__).with_name("test-210.rtp"))
    source = b"".join(data[12:] for role, data in records if role == 0)[:3 * COLUMNS * ROWS * BURST * 188]
    # All source, receiver logs and decoded artifacts are temporary, not repository fixtures.
    with tempfile.TemporaryDirectory(prefix="tsduck-gstreamer-fec-") as name:
        directory = Path(name)
        for mode, losses in (("1", {16, 17, 18, 19}), ("2", {17, 18, 21})):
            # Capture actual TSDuck output first and independently validate its complete wire data.
            # The external decoder receives unchanged parity from that capture, never a new oracle.
            records = fixture.capture(args.tsp, source, directory / "source.ts", mode, COLUMNS, ROWS, BURST)
            media, _ = fixture.verify(records, source, mode, COLUMNS, ROWS, False)
            check_receiver(args.gst_launch, records, media, source, directory, mode, losses)


if __name__ == "__main__":
    main()
