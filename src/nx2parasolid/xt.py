"""Decode and re-encode Parasolid neutral-binary transmits (``.x_b``), node by node.

Pure parsing - no Parasolid kernel. Layout (big-endian throughout)::

    "PS" 00 00 00
    u8  title length,  title       e.g. ": TRANSMIT FILE (partition) created by modeller version N"
    u32 key length,    schema key  e.g. "SCH_3801167_38101_13006"
    6 header bytes                 (copied unchanged)
    nodes, each:
        u16 node type              (1 = terminator)
        on the type's FIRST occurrence, its layout relative to the base schema:
            0xFF                      unchanged, or
            u8 n_fields, then edit codes, ending with 'Z':
                'C'                   copy the next base field
                'D'                   drop the next base field
                'I' | 'A'             insert/append a field: u8 len + name, u16 pointer class,
                                      u16 n_elements + 1, and if class == 0: u8 flag + u8 type char
            (base fields not reached before 'Z' are dropped)
          node types the base schema does not know are defined in full instead:
            u8 n_fields, u8 len + name, u8 len + description, field definitions as for 'I',
            u8 variable flag
        u32 n_entries              only for variable-length node types
        u16 index                  (pointers are node indices; 1 = null; a set high bit means a
                                    31-bit index in two u16)
        the transmitted fields, in layout order
    2 trailing bytes after the terminator (copied unchanged)

Field type characters: p pointer, d/t int32, u/c/l uint8, n/w uint16, f float64,
v vector (3 f64), h homogeneous vector (transmitted as 3 f64), b box (6 f64), i interval (2 f64).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .errors import XTDecodeError

MAGIC = b"PS\x00\x00\x00"
NULL = 1
HEADER_TAIL = 6

Layout = Tuple[List[Tuple[str, str, int]], int, str]   # (fields, variable, node name)


@dataclass
class Node:
    type: int
    name: str
    index: int
    nvar: int
    fields: Dict[str, object]


@dataclass
class Transmit:
    """A decoded transmit. ``layout``/``defbytes`` are what the file itself declared, so a
    re-encode reproduces the original schema information exactly."""

    header_text: bytes
    title: str
    schema_key: str
    header_tail: bytes
    nodes: List[Node]
    trailer: bytes
    layout: Dict[int, Layout] = field(default_factory=dict)
    defbytes: Dict[int, bytes] = field(default_factory=dict)

    @property
    def by_index(self) -> Dict[int, Node]:
        return {n.index: n for n in self.nodes}

    def nodes_named(self, name: str) -> List[Node]:
        return [n for n in self.nodes if n.name == name]


class _Reader:
    def __init__(self, data: bytes, schema: Dict[int, dict]) -> None:
        self.d = data
        self.p = 0
        self.base = schema
        self.layout: Dict[int, Layout] = {}
        self.defbytes: Dict[int, bytes] = {}

    def _need(self, n: int) -> None:
        if self.p + n > len(self.d):
            raise XTDecodeError(f"unexpected end of data at byte {self.p}")

    def u8(self) -> int:
        self._need(1)
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self) -> int:
        self._need(2)
        v = struct.unpack_from(">H", self.d, self.p)[0]
        self.p += 2
        return v

    def i32(self) -> int:
        self._need(4)
        v = struct.unpack_from(">i", self.d, self.p)[0]
        self.p += 4
        return v

    def f64(self, n: int = 1):
        self._need(8 * n)
        v = struct.unpack_from(f">{n}d", self.d, self.p)
        self.p += 8 * n
        return v if n > 1 else v[0]

    def s8(self) -> str:
        n = self.u8()
        self._need(n)
        v = self.d[self.p: self.p + n].decode("latin1")
        self.p += n
        return v

    def ptr(self) -> int:
        v = self.u16()
        if v & 0x8000:
            v = ((v & 0x7FFF) << 16) | self.u16()
        return v

    def value(self, t: str):
        if t == "p":
            return self.ptr()
        if t in "dt":
            return self.i32()
        if t in "ucl":
            return self.u8()
        if t in "nw":
            return self.u16()
        if t == "f":
            return self.f64()
        if t in "vh":
            return self.f64(3)
        if t == "b":
            return self.f64(6)
        if t == "i":
            return self.f64(2)
        raise XTDecodeError(f"unknown field type {t!r} at byte {self.p}")

    def _field_def(self, name: str) -> Tuple[str, str, int]:
        cls = self.u16()
        n = self.u16() - 1               # stored as n + 1 (1 = scalar)
        if cls == 0:
            self.u8()
            t = chr(self.u8())
        else:
            t = "p"
        return name, t, n

    def define(self, code: int) -> None:
        start = self.p
        base = self.base.get(code)
        flag = self.u8()
        if base is None:                  # node type newer than the base schema
            name = self.s8()
            self.s8()                     # description
            fields = [self._field_def(self.s8()) for _ in range(flag)]
            self.layout[code] = (fields, self.u8(), name)
            self.defbytes[code] = self.d[start: self.p]
            return
        bfields = [f for f in base["fields"] if f["xmt"]]

        def as_layout(f: dict) -> Tuple[str, str, int]:
            return f["name"], (f["type"] if f["cls"] == 0 else "p"), f["n"]

        if flag == 0xFF:
            fields = [as_layout(f) for f in bfields]
        else:
            fields, bi = [], 0
            while True:
                c = chr(self.u8())
                if c == "Z":
                    break
                if c == "C":
                    if bi >= len(bfields):
                        raise XTDecodeError(f"type {code}: copy past the end of the base fields")
                    fields.append(as_layout(bfields[bi]))
                    bi += 1
                elif c == "D":
                    bi += 1
                elif c in "IA":
                    fields.append(self._field_def(self.s8()))
                else:
                    raise XTDecodeError(f"bad schema edit code {c!r} at byte {self.p - 1} (type {code})")
            if len(fields) != flag:
                raise XTDecodeError(f"type {code}: expected {flag} fields, got {len(fields)}")
        self.layout[code] = (fields, base["variable"], base["name"])
        self.defbytes[code] = self.d[start: self.p]

    def node(self) -> Optional[Node]:
        code = self.u16()
        if code == NULL:
            return None
        if code not in self.layout:
            self.define(code)
        fields, variable, name = self.layout[code]
        nvar = self.i32() if variable else 0
        if nvar < 0:
            raise XTDecodeError(f"negative entry count at byte {self.p - 4}")
        index = self.ptr()
        vals: Dict[str, object] = {}
        last = len(fields) - 1
        for k, (fname, t, n) in enumerate(fields):
            if variable and k == last:
                if t == "c":
                    self._need(nvar)
                    vals[fname] = self.d[self.p: self.p + nvar]
                    self.p += nvar
                else:
                    vals[fname] = [self.value(t) for _ in range(nvar)]
            elif n > 1:
                vals[fname] = [self.value(t) for _ in range(n)]
            else:
                vals[fname] = self.value(t)
        return Node(code, name, index, nvar, vals)


def split_header(data: bytes) -> Tuple[bytes, bytes]:
    """Split a Parasolid file into (text header, binary transmit starting ``PS\\0\\0\\0``)."""
    i = data.find(MAGIC)
    if i < 0:
        raise XTDecodeError("not a Parasolid neutral-binary transmit (no 'PS' marker)")
    return data[:i], data[i:]


def schema_key_of(data: bytes) -> str:
    """The schema key of a transmit without decoding it."""
    _h, d = split_header(data)
    n = d[5]
    kn = struct.unpack_from(">i", d, 6 + n)[0]
    return d[10 + n: 10 + n + kn].decode("latin1")


def read_transmit(data: bytes, schema: Dict[int, dict]) -> Transmit:
    """Decode a complete Parasolid binary file (with or without its text header)."""
    header_text, d = split_header(data)
    r = _Reader(d, schema)
    r.p = 5
    title = r.s8()
    kn = r.i32()
    r._need(kn)
    key = d[r.p: r.p + kn].decode("latin1")
    r.p += kn
    r._need(HEADER_TAIL)
    tail = d[r.p: r.p + HEADER_TAIL]
    r.p += HEADER_TAIL
    nodes: List[Node] = []
    while True:
        nd = r.node()
        if nd is None:
            break
        nodes.append(nd)
    return Transmit(header_text, title, key, tail, nodes, d[r.p:], r.layout, r.defbytes)


class _Writer:
    def __init__(self) -> None:
        self.b = bytearray()

    def u8(self, v: int) -> None:
        self.b += bytes([v])

    def u16(self, v: int) -> None:
        self.b += struct.pack(">H", v)

    def i32(self, v: int) -> None:
        self.b += struct.pack(">i", v)

    def ptr(self, v: int) -> None:
        if v >= 0x8000:
            self.u16(0x8000 | (v >> 16))
            self.u16(v & 0xFFFF)
        else:
            self.u16(v)

    def value(self, t: str, v, remap: Callable[[int], int]) -> None:
        if t == "p":
            self.ptr(remap(v))
        elif t in "dt":
            self.i32(v)
        elif t in "ucl":
            self.u8(v)
        elif t in "nw":
            self.u16(v)
        elif t == "f":
            self.b += struct.pack(">d", v)
        elif t in "vbhi":
            self.b += struct.pack(f">{len(v)}d", *v)
        else:
            raise XTDecodeError(f"unknown field type {t!r}")


Keep = Callable[[Node, str, object], object]


def write_transmit(tx: Transmit, order: List[int], title: Optional[str] = None,
                   keep: Optional[Keep] = None, header_text: Optional[bytes] = None) -> bytes:
    """Encode the nodes with indices ``order`` (first = root) as a transmit.

    Nodes are renumbered 2..N in that order; a pointer to a node not in ``order`` becomes null.
    ``keep(node, field, value)`` may replace a field value (used to cut sibling links).
    Every other value is written exactly as decoded.
    """
    by = tx.by_index
    new_index = {old: k + 2 for k, old in enumerate(order)}

    def remap(x: int) -> int:
        return new_index.get(x, NULL)

    w = _Writer()
    w.b += MAGIC
    t = (tx.title if title is None else title).encode("latin1")
    w.u8(len(t))
    w.b += t
    k = tx.schema_key.encode("latin1")
    w.i32(len(k))
    w.b += k
    w.b += tx.header_tail
    defined = set()
    for old in order:
        nd = by[old]
        w.u16(nd.type)
        if nd.type not in defined:
            w.b += tx.defbytes[nd.type]
            defined.add(nd.type)
        fields, variable, _name = tx.layout[nd.type]
        if variable:
            w.i32(nd.nvar)
        w.ptr(new_index[old])
        last = len(fields) - 1
        for j, (fname, ft, n) in enumerate(fields):
            v = nd.fields[fname] if keep is None else keep(nd, fname, nd.fields[fname])
            if variable and j == last and ft == "c":
                w.b += v
            elif (variable and j == last) or n > 1:
                for x in v:
                    w.value(ft, x, remap)
            else:
                w.value(ft, v, remap)
    w.u16(NULL)
    w.b += tx.trailer
    return (tx.header_text if header_text is None else header_text) + bytes(w.b)
