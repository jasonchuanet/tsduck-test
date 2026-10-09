#!/usr/bin/env bash
#-----------------------------------------------------------------------------
#
# TSDuck - The MPEG Transport Stream Toolkit
# Copyright (c) 2026, Jason Chua
# BSD-2-Clause license, see LICENSE.txt file or https://tsduck.io/license
#
#-----------------------------------------------------------------------------
# Capture IP output on three local UDP ports and independently verify every parity byte.

source "$(dirname "$0")/../common/testrc.sh"
test_cleanup "$SCRIPT.*"

# Use the existing generated-media fixture, without invoking an external encoder.
# The test checks source ports, media preservation, wire fields and complete XOR payloads.
if ! "$PYTHON" "$(fpath "$INDIR/$SCRIPT.py")" --tsp "$(fpath "$(tspath tsp)")" \
    >"$OUTDIR/$SCRIPT.log" 2>&1; then
    cat >&2 "$OUTDIR/$SCRIPT.log"
    fail "SMPTE FEC transmission failed"
    exit "$EXIT_FAILURE"
fi

# Stable summaries omit ephemeral addresses, timestamps and scheduling observations.
# The emission schedule itself is checked with deterministic clocks in TSUnit.
test_text "$SCRIPT.log"
