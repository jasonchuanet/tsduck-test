# SMPTE ST 2022-1 FEC output regression

`test-211.py` captures the actual `ip` output plugin on loopback UDP ports and
checks every media and parity byte independently. It uses only the generated
MPEG-TS content from `test-210.rtp`; its provenance is in `test-210.md`.
No external encoder or decoder is needed for the normal regression:

```sh
tests/test-211.sh --bin /path/to/tsduck/bin/release-directory
```

The cases cover unchanged raw UDP and RTP, block-aligned and staggered 1D/2D FEC, explicit
none, default matrix geometry, both value spellings, maximum width and depth, a 100-datagram matrix, source IP/port
preservation, media sequence wrap, RS204, and a final short RTP payload.
The validator derives expected XOR payloads and recovery fields from captured
original media. It checks geometry, fixed and reserved fields, separate parity
sequence counters, exact media preservation, and the count of parity packets
permitted by either Annex B-style staggering or Annex C block interleaving.
The validator also checks the actual SNBase alignment of each column. Invalid profile, matrix
and destination-port combinations must fail with the relevant diagnostic.

The `RTPFECEncoderTest` TSUnit suite additionally covers every mandatory
ST 2022-2 matrix combination, the exact emission interval for each equation,
media and parity sequence wrap, timestamp wrap, variable payload lengths,
rejected input without state changes, restart, partial-group termination,
and burst/iterative reconstruction through the input decoder.

At termination, partial groups and columns needing future media are discarded.
The test expects no artificial media or prematurely emitted columns; final
media can consequently lack column protection.
The variable-length and short-final-burst cases exercise tolerant FEC compatibility.
They do not assert the ST 2022-2 requirement for a constant TS packet count per session;
strict constant-count transmission additionally needs enforced bursts and a whole-burst file length.

An optional Unix interoperability check uses an independent GStreamer receiver:

```sh
python3 input/test-211.gstreamer.py \
  --tsp /path/to/tsduck/bin/release-directory/tsp \
  --gst-launch gst-launch-1.0
```

It requires GStreamer's `rtpst2022-1-fecdec`, `rtpjitterbuffer`, `rtpmp2tdepay`
and `udpsrc` plugins. It replays unmodified TSDuck wire output, drops four
consecutive media datagrams in 1D mode and three media datagrams requiring
row/column recovery in 2D mode, for both alignment arrangements, and compares the full depayloaded TS byte for
byte. This was verified with GStreamer 1.26.2. GStreamer runs as a separate
program; no GStreamer or other external implementation code is bundled or
linked into the original BSD-2-Clause test harness or TSDuck feature.

Protocol references:

- [SMPTE ST 2022-1:2007](https://pub.smpte.org/pub/st2022-1/st2022-1-2007.pdf),
  sections 7, 8 and 9, and Annexes B and C: wire format, parity and transmission timing.
- [SMPTE ST 2022-2:2007](https://pub.smpte.org/pub/st2022-2/st2022-2-2007.pdf):
  MPEG-TS RTP profile and mandatory matrix sizes.
- [TSDuck issue #189](https://github.com/tsduck/tsduck/issues/189).

## Dektec naming and mode investigation

The IP output uses `--smpte-2022-fec mode`, `--smpte-2022-l` (columns) and
`--smpte-2022-d` (rows), matching the established Dektec option names.
The selector requires a value: `none`, `1d`, `1d-b`, `2d` or `2d-b`.
The `-b` suffix has the same block-alignment meaning as in Dektec.
IP input uses the `--smpte-2022-fec` flag, like Dektec input, and discovers
dimension and alignment from each parity header.

The September 2026 DTAPI Core Classes reference, in the official
[Dektec Linux SDK](https://www.dektec.com/downloads/SDK/), describes M1 as
inserting parity between media transmission slots and M2 as inserting it
immediately after a media packet. Their `-b` variants align columns.
These are Dektec API labels for standards-compliant packet insertion
choices, rather than distinct standardized FEC codes or 1D/2D selectors.
ST 2022-1 section 8.5 defines packet-spacing bounds and its informative
Annexes B/C illustrate non-block and block arrangements; it does not define
M1/M2 identifiers or require the hardware's sub-packet timing choices.
The IP implementation exposes dimension/alignment and rejects those vendor
labels rather than promising hardware timing it does not implement.
No SDK code, proprietary library or manual is included in either repository.

For general staggered geometry, column c starts at phase `(c % D)*L+c`
and repeats every `L*D` media. This agrees with the Annex B 4x5 example
(bases 0,5,10,15,20,...) and covers all mandatory geometries, including
L/D with a common factor and L greater than D. It bounds startup to one
matrix; each complete column is sent L intervals after its last member.
Incomplete startup columns and delayed termination columns are discarded.

Parser-level rejection messages are checked by the matching
`RTPFECOptionsTest` TSUnit suite with a synchronous report buffer. The CLI
regression requires exit status 1 for these cases, and still checks the text
of FEC-specific validation after parsing. This keeps FEC coverage independent
of TSP's inherited asynchronous diagnostic loss on parser-driven process exit.
The separate plugin-startup regression tests that logger lifecycle directly.
