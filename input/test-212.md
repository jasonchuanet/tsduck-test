# Plugin startup regression

This test requires the matching `fix/plugin-startup-diagnostics` TSDuck branch.
It has no dependency on the RTP FEC feature, network sockets, hardware or media
fixtures. It invokes the ordinary `tsp` and `tsswitch` commands with their default
asynchronous logger.

Each of 175 trials must exit with status 1 and deliver the expected diagnostic.
The cases cover invalid input, processing and output plugin options, missing
values, and values supplied to a flag. A lost diagnostic is a failure; trials
are never retried. Finite null input and subprocess timeouts bound accidental
acceptance and stalled processes. A valid `sections --version 3` command also
checks preservation of a plugin-defined option using a predefined name.

The corresponding TSUnit regressions verify `TSProcessor::start()` failure and
successful reuse after each plugin role fails, plus preservation of definitions
and values for all four reused predefined names across error-policy changes.
They also verify that changing the actual predefined-option flags still removes
the built-in options.

The Python and shell harnesses are original BSD-2-Clause code.
