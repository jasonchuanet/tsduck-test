#!/usr/bin/env bash
#-----------------------------------------------------------------------------
#
# TSDuck - The MPEG Transport Stream Toolkit
# Copyright (c) 2026, Jason Chua
# BSD-2-Clause license, see LICENSE.txt file or https://tsduck.io/license
#
#-----------------------------------------------------------------------------
# SMPTE ST 2022-1 1D/2D FEC reception by the ip input plugin (issue #189).

source "$(dirname "$0")/../common/testrc.sh"
test_cleanup "$SCRIPT.*"

# The fixed FFmpeg capture needs no encoder, external network or special
# privileges at test time. Python compares each recovered TS byte for byte.
if ! "$PYTHON" "$(fpath "$INDIR/$SCRIPT.py")" --tsp "$(fpath "$(tspath tsp)")" \
    >"$OUTDIR/$SCRIPT.log" 2>&1; then
    cat >&2 "$OUTDIR/$SCRIPT.log"
    fail "SMPTE FEC reception failed"
    exit "$EXIT_FAILURE"
fi

# Only deterministic case summaries enter the reference log. Dynamic ports,
# timestamps, sender SSRC and platform-specific socket errors are not logged.
test_text "$SCRIPT.log"
