"""Find the Parasolid transmits NX embeds in ``/Root/UG_PART/UG_PART``.

NX stores each body set as a zlib-compressed Parasolid *neutral binary* transmit, which starts
``PS\\0\\0\\0`` followed by a title such as ``": TRANSMIT FILE (partition) created by modeller
version 3801167"``. The first transmit is usually preceded by the standard plain-text Parasolid
header (``**ABCDEFGHIJ...**END_OF_HEADER***``). Three kinds occur:

* part transmits (``.x_b``)         - ordinary Parasolid part files, loadable as they are
* partitions (``.p_b``)             - a whole Parasolid partition; some bodies exist only here
* partition deltas (``.d_b``)       - undo history; not needed for geometry
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass
from typing import Iterator, List, Tuple

from .container import UG_PART, PrtFile
from .errors import PossiblyEncrypted

HEADER_START = b"**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ZLIB_HEADER = re.compile(rb"\x78[\x01\x5e\x9c\xda]")
_SCHEMA_KEY = re.compile(rb"SCH_[0-9_]+")

GENERIC_HEADER = (
    b"**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************\n"
    b"**PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************\n"
    b"**PART1;\nMC=^_;\nMC_MODEL=^_;\nMC_ID=^_;\nOS=^_;\nOS_RELEASE=^_;\nFRU=^_;\nAPPL=nx2parasolid;\n"
    b"SITE=^_;\nUSER=^_;\nFORMAT=binary;\nGUISE=transmit;\nKEY=part_file;\nFILE=^_;\nDATE=^_;\n"
    b"**PART2;\nSCH=%s;\nUSFLD_SIZE=0;\n**PART3;\n"
    b"**END_OF_HEADER*****************************************************************\n"
)

KIND_EXT = {"part": ".x_b", "partition": ".p_b", "deltas": ".d_b"}


@dataclass
class ParasolidStream:
    """One Parasolid transmit found in the part file."""

    index: int
    kind: str              # "part" | "partition" | "deltas"
    title: str
    schema_key: str
    header: bytes          # plain-text Parasolid header to write in front of ``body``
    body: bytes            # the decompressed transmit, starting b"PS\0\0\0"
    offset: int            # offset of the zlib data inside UG_PART
    compressed_size: int

    @property
    def data(self) -> bytes:
        """A complete Parasolid file (text header + binary transmit)."""
        return self.header + self.body

    @property
    def ext(self) -> str:
        return KIND_EXT[self.kind]


def iter_zlib(blob: bytes) -> Iterator[Tuple[int, int, bytes]]:
    """Yield ``(offset, compressed_size, payload)`` for every complete zlib stream in ``blob``."""
    pos = 0
    for m in _ZLIB_HEADER.finditer(blob):
        s = m.start()
        if s < pos:
            continue
        z = zlib.decompressobj()
        try:
            payload = z.decompress(blob[s:])
        except zlib.error:
            continue
        if not z.eof:
            continue
        used = len(blob) - s - len(z.unused_data)
        pos = s + used
        yield s, used, payload


def _kind(title: str) -> str:
    if "(partition)" in title:
        return "partition"
    if "(deltas)" in title:
        return "deltas"
    return "part"


def extract_streams(prt: PrtFile) -> List[ParasolidStream]:
    """All Parasolid transmits in the part, in file order.

    Raises :class:`PossiblyEncrypted` when a Parasolid header is followed by data that is not
    zlib - nx2parasolid does not attempt to read protected data.
    """
    ug = prt.stream(UG_PART)
    own_header = b""
    hs = ug.find(HEADER_START)
    if hs >= 0:
        end = ug.find(b"**END_OF_HEADER", hs)
        he = ug.find(b"\n", end) + 1 if end >= 0 else 0
        if he <= 0:
            raise PossiblyEncrypted("Parasolid text header is not terminated")
        own_header = ug[hs:he]
        if 0x78 not in ug[he: he + 5]:
            raise PossiblyEncrypted("data after the Parasolid header is not zlib; stopping")
    out: List[ParasolidStream] = []
    for offset, used, payload in iter_zlib(ug):
        if not payload.startswith(b"PS\x00\x00\x00") or len(payload) < 7:
            continue
        n = payload[5]
        title = payload[6: 6 + n].decode("latin1", "replace")
        # u32 (big-endian) length + schema key, e.g. "SCH_3801167_38101_13006"
        kn = int.from_bytes(payload[6 + n: 10 + n], "big")
        key = payload[10 + n: 10 + n + kn].decode("latin1", "replace") if 0 < kn < 128 else ""
        if not key.startswith("SCH_"):
            m = _SCHEMA_KEY.search(payload[: 6 + n + 64])
            key = m.group().decode() if m else ""
        # NX's text header names the schema without the base suffix: "SCH=SCH_3801167_38101;"
        short = "_".join(key.split("_")[:3]).encode()
        header = own_header if own_header and short and short in own_header \
            else GENERIC_HEADER.replace(b"%s", short)
        out.append(ParasolidStream(len(out), _kind(title), title, key, header, payload, offset, used))
    return out
