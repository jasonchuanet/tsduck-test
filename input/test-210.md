# SMPTE 2022-1 FEC regression capture

`test-210.rtp` contains 421 UDP payloads from a four-second FFmpeg 7.1.5
Pro-MPEG sender with a 4x4 FEC matrix. The video is the generated `testsrc2`
pattern, encoded as MPEG-2 video. There is no external video or audio source.
FFmpeg runs as a separate program; no FFmpeg implementation code is included.
The test harness is original BSD-2-Clause code.

Each record has a one-byte stream number (0 = media, 1 = column FEC,
2 = row FEC), a two-byte big-endian payload length, and that many UDP payload
bytes, including the RTP header. The file starts with `TSDUCK-FEC-1` and a
newline. Original network addresses, UDP ports and capture timestamps are
omitted so that replay works with arbitrary local ports.

The normal test does not require FFmpeg:

```sh
tests/test-210.sh --bin /path/to/tsduck/bin/release-directory
```

The `--smpte-2022-fec` input flag automatically handles both wire dimensions.
The test checks raw UDP and RTP input, 1D burst recovery, iterative 2D recovery,
uncorrectable loss, lost and malformed parity, buffer pressure, explicit media
source filtering, multicast, receive timeout with idle parity sockets,
and flag/dependent-option validation.
Each output transport stream is compared byte for byte with the captured media
payloads, excluding only the intentionally unrecoverable losses.
Replay uses a one-second FEC latency to allow host scheduling pauses on CI
runners. Deadline expiry is checked with a simulated clock in the decoder unit
tests, independently of operating-system scheduling.

The companion decoder tests in TSDuck cover all mandatory ST 2022-2 matrix
sizes, staggered columns, parity before media, duplicate and reordered media,
RTP sequence and timestamp wrap, 188/204-byte TS, malformed headers, bounded
memory, late parity and iterative recovery. Run `RTPFECDecoderTest` using TSUnit.

To regenerate the capture explicitly:

```sh
python3 input/test-210.py --capture --ffmpeg ffmpeg
```

The capture command is:

```sh
ffmpeg -hide_banner -nostdin -loglevel error -re \
  -f lavfi -i testsrc2=size=160x120:rate=25 -t 4 \
  -c:v mpeg2video -b:v 500k -muxrate 2M \
  -f rtp_mpegts -fec prompeg=l=4:d=4 \
  'rtp://127.0.0.1:PORT?pkt_size=1328'
```

Regeneration changes random RTP identifiers, timestamps and possibly encoder
output. Review the new capture and regenerate the case summary reference using
`tests/test-210.sh --init` before committing it.

Protocol references:

- [SMPTE ST 2022-1:2007](https://pub.smpte.org/pub/st2022-1/st2022-1-2007.pdf),
  sections 7 and 8: FEC header and row/column XOR equations.
- [SMPTE ST 2022-2:2007](https://pub.smpte.org/pub/st2022-2/st2022-2-2007.pdf):
  MPEG-TS RTP profile and required matrix sizes.
- [TSDuck issue #189](https://github.com/tsduck/tsduck/issues/189).

Parser-level rejection messages are checked by the matching
`RTPFECOptionsTest` TSUnit suite with a synchronous report buffer. The CLI
regression requires exit status 1 for these cases, and still checks the text
of FEC-specific validation after parsing. This keeps FEC coverage independent
of TSP's inherited asynchronous diagnostic loss on parser-driven process exit.
The separate plugin-startup regression tests that logger lifecycle directly.
