# SMPTE ST 2022-1 FEC output regression

`test-211.py` captures the actual `ip` output plugin on loopback UDP ports and
checks every media and parity byte independently. It uses only the generated
MPEG-TS content from `test-210.rtp`; its provenance is in `test-210.md`.
No external encoder or decoder is needed for the normal regression:

```sh
tests/test-211.sh --bin /path/to/tsduck/bin/release-directory
```

The cases cover unchanged raw UDP and RTP, 1D column FEC, default and explicit
2D FEC, maximum width and depth, a 100-datagram matrix, source IP/port
preservation, media sequence wrap, RS204, and a final short RTP payload.
The validator derives expected XOR payloads and recovery fields from captured
original media. It checks geometry, fixed and reserved fields, separate parity
sequence counters, exact media preservation, and the count of parity packets
permitted by the aligned Annex C emission schedule. Invalid profile, matrix
and destination-port combinations must fail with the relevant diagnostic.

The `RTPFECEncoderTest` TSUnit suite additionally covers every mandatory
ST 2022-2 matrix combination, the exact emission interval for each equation,
media and parity sequence wrap, timestamp wrap, variable payload lengths,
rejected input without state changes, restart, partial-group termination,
and burst/iterative reconstruction through the input decoder.

At termination, partial groups and columns needing future media are discarded.
The test expects no artificial media or prematurely emitted columns; final
media can consequently lack column protection.

An optional Unix interoperability check uses an independent GStreamer receiver:

```sh
python3 input/test-211.gstreamer.py \
  --tsp /path/to/tsduck/bin/release-directory/tsp \
  --gst-launch gst-launch-1.0
```

It requires GStreamer's `rtpst2022-1-fecdec`, `rtpjitterbuffer`, `rtpmp2tdepay`
and `udpsrc` plugins. It replays unmodified TSDuck wire output, drops four
consecutive media datagrams in 1D mode and three media datagrams requiring
row/column recovery in 2D mode, and compares the full depayloaded TS byte for
byte. This was verified with GStreamer 1.26.2. GStreamer runs as a separate
program; no GStreamer or other external implementation code is bundled or
linked into the original BSD-2-Clause test harness or TSDuck feature.

Protocol references:

- [SMPTE ST 2022-1:2007](https://pub.smpte.org/pub/st2022-1/st2022-1-2007.pdf),
  sections 7, 8 and 9, and Annex C: wire format, parity and transmission timing.
- [SMPTE ST 2022-2:2007](https://pub.smpte.org/pub/st2022-2/st2022-2-2007.pdf):
  MPEG-TS RTP profile and mandatory matrix sizes.
- [TSDuck issue #189](https://github.com/tsduck/tsduck/issues/189).
