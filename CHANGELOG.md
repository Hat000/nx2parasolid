# Changelog

## 0.1.0 - 2026-11-25

First release.

* `convert`: NX `.prt` -> Parasolid. NX's own part transmits are copied byte-for-byte; bodies that
  exist only in the partition store are re-emitted as stand-alone part transmits; faceless
  construction bodies are skipped; every output is decoded again as a self-check.
* `--solidworks` (Windows): merge each part into one `.x_t` / `.SLDPRT`, keeping the bodies NX
  displays, run Check, re-import and compare every body (PASS / CHECK).
* `info`, `tree`, `preview`, `schemas` commands.
* Pure Python, no dependencies; Parasolid schema files are located on the machine, not shipped.
* Synthetic test suite (no CAD data, no CAD system needed).
