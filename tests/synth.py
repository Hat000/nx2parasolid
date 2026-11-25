"""Build synthetic NX files for the tests: a small made-up Parasolid-style schema, transmits
encoded with it, and an SPLMSSTR container around them. Nothing here comes from Siemens or from
real CAD data; the node types only mimic the shape of the real format."""
from __future__ import annotations

import struct
import uuid
import zlib
from typing import Dict, List, Tuple

from nx2parasolid.xt import NULL, Node, Transmit, write_transmit

BASE_ID = "99001"
SCHEMA_KEY = f"SCH_1000_100_{BASE_ID}"
SCHEMA_SHORT = "SCH_1000_100"

# code NAME; description; transmitted n_fields variable  /  name; type; transmitted ptr_class n
SCHEMA_TEXT = f"""**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz** synthetic test schema
: SCHEMA FILE {BASE_ID};
12 BODY; Body; 1 7 0
next; p; 1 12 1
previous; p; 1 12 1
owner; p; 1 101 1
body_type; u; 1 0 1
region; p; 1 19 1
scratch; d; 0 0 1
shell; p; 1 13 1
19 REGION; Region; 1 2 0
next; p; 1 19 1
shell; p; 1 13 1
13 SHELL; Shell; 1 2 0
next; p; 1 13 1
face; p; 1 14 1
14 FACE; Face; 1 4 0
next; p; 1 14 1
surface; p; 1 50 1
tolerance; f; 1 0 1
sense; c; 1 0 1
50 PLANE; Plane; 1 3 0
pos; v; 1 0 1
normal; v; 1 0 1
box; b; 1 0 1
74 POINTER_LIS_BLOCK; Pointer list; 1 2 1
n_entries; d; 1 0 1
entries; p; 1 1001 1
81 NAME_TEXT; Name text; 1 1 1
text; c; 1 0 1
101 WORLD; World; 1 2 0
body; p; 1 12 1
list; p; 1 74 1
"""


def schema_dict() -> Dict[int, dict]:
    from nx2parasolid.schema import parse_schema_text
    return parse_schema_text(SCHEMA_TEXT)


def layout_for(schema: Dict[int, dict], code: int):
    base = schema[code]
    fields = [(f["name"], f["type"] if f["cls"] == 0 else "p", f["n"]) for f in base["fields"] if f["xmt"]]
    return fields, base["variable"], base["name"]


class Builder:
    """Collect nodes, then encode them as a transmit with every type declared 'unchanged'."""

    def __init__(self, title=": TRANSMIT FILE created by modeller version 1000"):
        self.schema = schema_dict()
        self.nodes: List[Node] = []
        self.title = title
        self.extra_layout: Dict[int, tuple] = {}
        self.extra_defbytes: Dict[int, bytes] = {}

    def add(self, code: int, index: int, nvar: int = 0, **fields) -> Node:
        name = self.schema[code]["name"] if code in self.schema else self.extra_layout[code][2]
        n = Node(code, name, index, nvar, fields)
        self.nodes.append(n)
        return n

    def transmit(self) -> Transmit:
        layout = {c: layout_for(self.schema, c) for c in self.schema}
        layout.update(self.extra_layout)
        defbytes = {c: b"\xff" for c in self.schema}
        defbytes.update(self.extra_defbytes)
        return Transmit(b"", self.title, SCHEMA_KEY, b"\x00" * 6, self.nodes, b"\x00\x00", layout, defbytes)

    def encode(self, header: bytes = b"") -> bytes:
        tx = self.transmit()
        return write_transmit(tx, [n.index for n in self.nodes], header_text=header)


def add_body(b: Builder, start: int, faces: int, owner: int = NULL, prev: int = NULL, nxt: int = NULL) -> int:
    """A body with one region, one shell and ``faces`` planar faces; indices from ``start``."""
    body, region, shell = start, start + 1, start + 2
    b.add(12, body, next=nxt, previous=prev, owner=owner, body_type=1, region=region,
          shell=shell if faces else NULL)
    b.add(19, region, next=NULL, shell=shell if faces else NULL)
    face0 = start + 3
    b.add(13, shell, next=NULL, face=face0 if faces else NULL)
    i = face0
    for k in range(faces):
        surf = i + 1
        b.add(14, i, next=(i + 2 if k < faces - 1 else NULL), surface=surf, tolerance=1e-8, sense=43)
        b.add(50, surf, pos=(0.0, 0.0, float(k)), normal=(0.0, 0.0, 1.0), box=(0, 0, 0, 1, 1, 1))
        i += 2
    return i


def part_transmit(faces: int = 3) -> bytes:
    b = Builder()
    add_body(b, 2, faces)
    return b.encode()


def partition_transmit(face_counts=(2, 0, 4)) -> bytes:
    """A partition: WORLD -> pointer list of bodies; bodies chained next/previous, owned by WORLD."""
    b = Builder(": TRANSMIT FILE (partition) created by modeller version 1000")
    world = 2
    starts, i = [], 10
    for fc in face_counts:
        starts.append(i)
        i += 3 + 2 * max(fc, 0) + 1
    b.add(101, world, body=starts[0], list=3)
    b.add(74, 3, nvar=len(starts), n_entries=len(starts), entries=list(starts))
    b.add(81, 4, nvar=5, text=b"hello")
    for k, (s, fc) in enumerate(zip(starts, face_counts)):
        add_body(b, s, fc, owner=world, prev=starts[k - 1] if k else NULL,
                 nxt=starts[k + 1] if k + 1 < len(starts) else NULL)
    return b.encode()


TEXT_HEADER = (b"**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************\n"
               b"**PARASOLID test header\n**PART2;\nSCH=" + SCHEMA_SHORT.encode() + b";\n**PART3;\n"
               b"**END_OF_HEADER*****************************************************************\n")


def ug_part(transmits: List[bytes], with_header: bool = True, junk_after_header: bool = False) -> bytes:
    out = b"\x00\x01NXMODEL" + (TEXT_HEADER if with_header else b"")
    if junk_after_header:
        return out + b"\x13\x37" * 64
    for t in transmits:
        out += b"\x00" + zlib.compress(t) + b"\x00\x00"
    return out


def jt_segment(shapes: List[Tuple[List[float], float]]) -> bytes:
    """A JT segment holding one Tri-Strip Set Shape element per (bbox_m, area_m2)."""
    g = uuid.UUID("10dd1077-2ac8-11d1-9b6b-0080c7bb5997").bytes_le
    out = b""
    for bbox, area in shapes:
        payload = b"\x00" * 16 + struct.pack("<6f", *bbox) + b"\x00" * 24 + struct.pack("<f", area)
        elem = g + b"\x01" + payload
        out += struct.pack("<i", len(elem)) + elem
    return zlib.compress(out)


def structure_stream(components: List[str]) -> bytes:
    def lst(items):
        b = bytes([1, len(items) + 1])
        for s in items:
            e = s.encode()
            b += bytes([4, len(e) + 2]) + e + b"\x00"
        return b
    return (b"\x00UGS::Assy::Structure\x00" + lst(["Arrangement 1"]) + lst(["MODEL", "Entire Part", "Empty"])
            + b"\xff\xff\xff\xff" + (lst(components) if components else b"") + b"\x00I/Chronological/\x00")


def make_prt(streams: Dict[str, bytes]) -> bytes:
    """An SPLMSSTR container: magic, directory, then the stream data."""
    names = list(streams)
    dir_size = sum(4 + len(n.encode()) + 16 for n in names)
    pos = 16 + dir_size
    directory, data = b"", b""
    for n in names:
        nb = n.encode()
        directory += struct.pack("<I", len(nb)) + nb + struct.pack("<QQ", pos, len(streams[n]))
        data += streams[n]
        pos += len(streams[n])
    return b"SPLMSSTR" + b"\x00" * 8 + directory + data
