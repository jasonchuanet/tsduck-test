#!/usr/bin/env bash
#-----------------------------------------------------------------------------
#
# TSDuck - The MPEG Transport Stream Toolkit
# Copyright (c) 2026, Jason Chua
# BSD-2-Clause license, see LICENSE.txt file or https://tsduck.io/license
#
#-----------------------------------------------------------------------------
# Check default asynchronous startup diagnostics and custom plugin options.

source "$(dirname "$0")/../common/testrc.sh"
test_cleanup "$SCRIPT.*"

# Resolve both development commands using the normal cross-platform framework.
# The Python test needs neither FEC nor a network or external media fixture.
if ! "$PYTHON" "$(fpath "$INDIR/$SCRIPT.py")" --tsp "$(fpath "$(tspath tsp)")" \
    --tsswitch "$(fpath "$(tspath tsswitch)")" >"$OUTDIR/$SCRIPT.log" 2>&1; then
    cat >&2 "$OUTDIR/$SCRIPT.log"
    fail "Plugin startup regression failed"
    exit "$EXIT_FAILURE"
fi

# Summaries are stable across hosts; every child status and diagnostic is checked.
# None of the repetitions tolerates or retries a lost log message.
test_text "$SCRIPT.log"
