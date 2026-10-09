#!/usr/bin/env python3
#-----------------------------------------------------------------------------
#
# TSDuck - The MPEG Transport Stream Toolkit
# Copyright (c) 2026, Jason Chua
# BSD-2-Clause license, see LICENSE.txt file or https://tsduck.io/license
#
#-----------------------------------------------------------------------------
# Exercise startup errors using the CLI's default asynchronous report.

import argparse
import subprocess


def reject(tool, options, message):
    """Require both the failure status and its queued diagnostic on every run."""
    # Do not enable synchronous logging, sleep, or retry a lost diagnostic.
    # The ordinary process shutdown must drain its report before returning.
    # Preserve original bytes in failure reports, including empty stderr.
    result = subprocess.run([tool, *options], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, timeout=5)
    # A finite source also bounds any configuration accidentally accepted.
    # Status alone could accept an unrelated socket, loader or runtime failure.
    if result.returncode != 1 or message not in result.stderr.decode(errors="replace"):
        raise RuntimeError(f"Invalid startup result: {options}, exit={result.returncode}, stderr={result.stderr!r}")


def main():
    # The shell harness chooses development tools from the matching branch.
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsp", required=True)
    parser.add_argument("--tsswitch", required=True)
    args = parser.parse_args()
    # No FEC option, UDP socket, device or external media fixture is used here.
    # The five plugin roles exercise both owners of PluginThread.
    cases = [
        (args.tsp, ["-I", "null", "1", "--invalid-plugin-option", "-O", "drop"], "unknown option"),
        (args.tsp, ["-I", "null", "1", "-P", "until", "--invalid-plugin-option", "-O", "drop"], "unknown option"),
        (args.tsp, ["-I", "null", "1", "-O", "drop", "--invalid-plugin-option"], "unknown option"),
        (args.tsswitch, ["-I", "null", "1", "--invalid-plugin-option", "-O", "drop"], "unknown option"),
        (args.tsswitch, ["-I", "null", "1", "-O", "drop", "--invalid-plugin-option"], "unknown option"),
        # Other parser error categories use the same startup failure path.
        (args.tsp, ["-I", "null", "1", "-P", "filter", "--pid", "-O", "drop"], "missing value"),
        (args.tsp, ["-I", "null", "1", "-P", "filter", "--negate=bad", "-O", "drop"], "no value allowed"),
    ]
    # Repetition exercises the scheduling-sensitive producer/logger race.
    # Every trial must pass; this is not a retry which discards failures.
    for _ in range(25):
        for tool, options, message in cases:
            reject(tool, options, message)
    print(f"PASS startup-errors: {25 * len(cases)} asynchronous diagnostics and exit codes verified")
    # sections defines its own --version, instead of the built-in version flag.
    # Changing parser error policy must preserve that definition and its value.
    result = subprocess.run([args.tsp, "-I", "null", "1", "-P", "sections", "--pid", "0", "--version", "3", "-O", "drop"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f"Custom plugin option lost: exit={result.returncode}, stderr={result.stderr!r}")
    print("PASS custom-options: sections --version remains available")


if __name__ == "__main__":
    # Failures reach the existing shell harness with their useful explanation.
    main()
