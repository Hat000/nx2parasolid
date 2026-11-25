"""Assembly structure and thumbnail of an NX part.

``/Root/FastLoad/Structure`` stores string lists as::

    0x01, n + 1, then n strings, each:  0x04, length (= characters + 2), UTF-8 text, 0x00

Several lists occur (arrangements, reference-set names, per-occurrence reference sets); the
part files a part uses are the last list before the ``I/Chronological/`` section that is not a
reference-set or arrangement list. The list gives each referenced part **once**, however many
times it is used, and **no placements** (those are in NX's encoded object data, not read here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .container import FASTLOAD_STRUCTURE, PREVIEW, UG_PART, PrtFile
from .errors import NotAnNXFile

_REFSET_WORDS = {"MODEL", "None", "Empty", "Entire Part"}


def _tagged_string(s: bytes, i: int) -> Optional[Tuple[str, int]]:
    if i + 2 > len(s) or s[i] != 0x04:
        return None
    n = s[i + 1]
    if n < 2 or i + n >= len(s) or s[i + n] != 0:
        return None
    try:
        text = s[i + 2: i + n].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return (text, i + n + 1) if text and text.isprintable() else None


def string_lists(s: bytes) -> List[Tuple[int, List[str]]]:
    """Every counted string list in a Structure stream, as (offset, strings)."""
    out: List[Tuple[int, List[str]]] = []
    for i in range(len(s) - 2):
        if s[i] != 0x01 or s[i + 1] < 2:
            continue
        want, j, items = s[i + 1] - 1, i + 2, []
        for _ in range(want):
            r = _tagged_string(s, j)
            if r is None:
                break
            items.append(r[0])
            j = r[1]
        if len(items) == want:
            out.append((i, items))
    return out


def component_names(prt: PrtFile, own_name: Optional[str] = None) -> List[str]:
    """Names of the part files this part references (each once), in NX's order."""
    s = prt.stream(FASTLOAD_STRUCTURE)
    end = s.find(b"I/Chronological/")
    best: List[str] = []
    for off, items in string_lists(s):
        if end >= 0 and off > end:
            break
        if any(x in _REFSET_WORDS or "/" in x for x in items):
            continue
        if all(x.startswith(("Arrangement", "REFERENCE_SET")) for x in items):
            continue
        best = items
    own = (own_name or prt.name).upper()
    return [x for x in best if x.upper() != own]


def preview_jpeg(prt: PrtFile) -> bytes:
    """The part's thumbnail (JPEG bytes), or ``b""``."""
    return prt.stream(PREVIEW)


@dataclass
class TreeNode:
    name: str
    path: Optional[Path]
    has_geometry_stream: bool = False
    children: List[TreeNode] = field(default_factory=list)
    repeated: bool = False      # already expanded elsewhere in the tree

    @property
    def missing(self) -> bool:
        return self.path is None


def _index_folder(folder: Path) -> Dict[str, Path]:
    return {p.stem.upper(): p for p in folder.glob("*.prt")}


def build_tree(top: Path, folder: Optional[Path] = None) -> TreeNode:
    """Walk the assembly from ``top``, resolving children to ``.prt`` files in ``folder``
    (default: the folder of ``top``). Children without a file are marked missing."""
    folder = folder or top.parent
    files = _index_folder(folder)
    seen: Dict[str, TreeNode] = {}

    def walk(name: str, path: Optional[Path]) -> TreeNode:
        key = name.upper()
        if path is None:
            path = files.get(key)
        if path is None:
            return TreeNode(name, None)
        if key in seen:
            return TreeNode(seen[key].name, path, seen[key].has_geometry_stream, [], True)
        try:
            prt = PrtFile.open(path)
        except NotAnNXFile:
            return TreeNode(name, None)
        node = TreeNode(path.stem, path, prt.has(UG_PART))
        seen[key] = node
        for child in component_names(prt, path.stem):
            node.children.append(walk(child, None))
        return node

    return walk(top.stem, top)
