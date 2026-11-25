"""Face and surface census of every body in a decoded transmit (no CAD system involved).

Walks BODY -> REGION chain -> SHELL chain -> FACE chain -> surface node, the same topology a
Parasolid kernel would report. Bodies with no faces are point/wire construction bodies.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Set

from .xt import NULL, Node, Transmit


@dataclass
class BodyCensus:
    index: int
    body_type: int
    faces: int
    shells: int
    surfaces: Dict[str, int] = field(default_factory=dict)

    @property
    def has_faces(self) -> bool:
        return self.faces > 0


def _chain(by: Dict[int, Node], start: int, link: str, seen: Set[int]) -> List[Node]:
    out: List[Node] = []
    i = start
    while i in by and i != NULL and i not in seen:
        seen.add(i)
        out.append(by[i])
        i = by[i].fields.get(link, NULL)
    return out


def census(tx: Transmit) -> List[BodyCensus]:
    by = tx.by_index
    rows: List[BodyCensus] = []
    for body in tx.nodes_named("BODY"):
        seen: Set[int] = set()
        shells: List[Node] = []
        for region in _chain(by, body.fields.get("region", NULL), "next", seen):
            shells += _chain(by, region.fields.get("shell", NULL), "next", seen)
        faces: List[Node] = []
        for sh in shells:
            faces += _chain(by, sh.fields.get("face", NULL), "next", seen)
        kinds: Counter = Counter()
        for fc in faces:
            s = by.get(fc.fields.get("surface", NULL))
            kinds[s.name if s else "none"] += 1
        rows.append(BodyCensus(body.index, int(body.fields.get("body_type", 0) or 0),
                               len(faces), len(shells), dict(sorted(kinds.items()))))
    return rows
