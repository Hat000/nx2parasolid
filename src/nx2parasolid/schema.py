"""Locate and parse Parasolid schema files.

A neutral-binary transmit does not describe its own node layouts in full: it names a schema
(``SCH_<modeller>_<version>_<base>``) and embeds only the *differences* from the base schema
``<base>``. Decoding therefore needs the base schema text file, ``sch_<base>.s_t``.

Those files are part of the Parasolid runtime that Parasolid-based CAD systems install (for
example SolidWorks: ``<install>\\data\\pschema``). nx2parasolid does **not** ship them; it looks
for them, in order:

1. the ``--schema-dir`` option / ``schema_dir`` argument (a directory, or one schema file)
2. the ``NX2PARASOLID_SCHEMA_DIR`` environment variable
3. the ``P_SCHEMA`` environment variable (Parasolid's own convention)
4. known install locations (SolidWorks, eDrawings, Parasolid)
"""
from __future__ import annotations

import glob
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Union

from .errors import SchemaNotFound

ENV_VAR = "NX2PARASOLID_SCHEMA_DIR"
_FILE_RE = re.compile(r"^sch_(\d+)\.(s_t|sch_txt)$", re.IGNORECASE)
_NODE_RE = re.compile(r"^(\d+) ([A-Z0-9_]+); (.*?); (\d+) (\d+) (\d+)\s*$")
_FIELD_RE = re.compile(r"^([a-z_0-9]+); ([a-z]); (\d+) (\d+) (\d+)\s*$")

_KNOWN_GLOBS = [
    "C:/Program Files/SOLIDWORKS Corp*/SOLIDWORKS*/data/pschema",
    "C:/Program Files/SOLIDWORKS Corp*/eDrawings*/pschema",
    "C:/Program Files/Common Files/SOLIDWORKS Shared/*/pschema",
    "C:/Program Files/Siemens/*/*/schema",
    "C:/Program Files/Siemens/*/schema",
    "/opt/parasolid/*/schema",
]


def base_schema_id(schema_key: str) -> str:
    """``"SCH_3801167_38101_13006"`` -> ``"13006"`` (the base the transmit's edits apply to)."""
    parts = schema_key.strip().split("_")
    if len(parts) < 2 or not parts[-1].isdigit():
        raise SchemaNotFound(f"unrecognised schema key {schema_key!r}")
    return parts[-1]


def parse_schema_text(text: str) -> Dict[int, dict]:
    """Parse a Parasolid schema text file into ``{node_code: node definition}``.

    Node line:  ``<code> <NAME>; <description>; <transmitted> <n_fields> <variable>``
    Field line: ``<name>; <type char>; <transmitted> <pointer class> <n_elements>``
    """
    nodes: Dict[int, dict] = {}
    cur: Optional[dict] = None
    for raw in text.splitlines():
        ln = raw.strip()
        m = _NODE_RE.match(ln)
        if m:
            cur = dict(code=int(m.group(1)), name=m.group(2), xmt=int(m.group(4)),
                       nfields=int(m.group(5)), variable=int(m.group(6)), fields=[])
            nodes[cur["code"]] = cur
            continue
        f = _FIELD_RE.match(ln)
        if f and cur is not None:
            cur["fields"].append(dict(name=f.group(1), type=f.group(2), xmt=int(f.group(3)),
                                      cls=int(f.group(4)), n=int(f.group(5))))
    if not nodes:
        raise SchemaNotFound("no node definitions found in schema text")
    return nodes


def _candidates(explicit: Optional[Union[str, Path]]) -> List[Path]:
    c: List[Path] = []
    if explicit:
        c.append(Path(explicit))
    for var in (ENV_VAR, "P_SCHEMA"):
        if os.environ.get(var):
            c.append(Path(os.environ[var]))
    for g in _KNOWN_GLOBS:
        c.extend(Path(p) for p in sorted(glob.glob(g), reverse=True))
    return c


class SchemaStore:
    """Schema files available on this machine, loaded on demand and cached."""

    def __init__(self, schema_dir: Optional[Union[str, Path]] = None) -> None:
        self.files: Dict[str, Path] = {}
        self.searched: List[Path] = _candidates(schema_dir)
        for cand in self.searched:
            if cand.is_file():
                m = _FILE_RE.match(cand.name)
                if m:
                    self.files.setdefault(m.group(1), cand)
            elif cand.is_dir():
                for p in cand.iterdir():
                    m = _FILE_RE.match(p.name)
                    if m:
                        self.files.setdefault(m.group(1), p)
        self._cache: Dict[str, Dict[int, dict]] = {}

    def available(self) -> List[str]:
        return sorted(self.files, key=int)

    def path_for(self, schema_key: str) -> Path:
        base = base_schema_id(schema_key)
        if base not in self.files:
            where = ", ".join(str(p) for p in self.searched) or "nowhere"
            raise SchemaNotFound(
                f"Parasolid schema sch_{base}.s_t (for {schema_key}) not found. Searched: {where}. "
                f"Point --schema-dir (or {ENV_VAR}) at a directory holding Parasolid schema files, "
                f"e.g. <SOLIDWORKS install>\\data\\pschema.")
        return self.files[base]

    def get(self, schema_key: str) -> Dict[int, dict]:
        base = base_schema_id(schema_key)
        if base not in self._cache:
            self._cache[base] = parse_schema_text(self.path_for(schema_key).read_text("latin1"))
        return self._cache[base]
