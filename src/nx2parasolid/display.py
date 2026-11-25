"""Bodies NX displays for a part, from its lightweight display data (``/Root/FastLoad/JT``).

The JT data is a set of zlib-compressed segments. Inside them, each displayed body has a
*Tri-Strip Set Shape* node (GUID ``10dd1077-2ac8-11d1-9b6b-0080c7bb5997``) whose payload gives
the body's bounding box and surface area::

    I32 element length, GUID (16 bytes, little-endian), U8 base type, payload:
      payload[16:40]  6 x f32  transformed bounding box (metres)
      payload[64:68]  f32      surface area (m^2)

nx2parasolid uses these to keep the bodies NX shows and leave out construction, reference and
linked bodies the part also carries.
"""
from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from typing import List

from .container import FASTLOAD_JT, PrtFile
from .streams import iter_zlib

TRI_STRIP_SHAPE = "10dd1077"


@dataclass
class DisplayShape:
    segment: int
    bbox_mm: List[float]
    area_mm2: float


def display_shapes(prt: PrtFile) -> List[DisplayShape]:
    out: List[DisplayShape] = []
    for seg, (_off, _used, x) in enumerate(iter_zlib(prt.stream(FASTLOAD_JT))):
        p = 0
        while p + 21 <= len(x):
            (length,) = struct.unpack_from("<i", x, p)
            if length <= 0 or p + 4 + length > len(x):
                break
            guid = str(uuid.UUID(bytes_le=x[p + 4: p + 20]))
            body = x[p + 21: p + 4 + length]
            if guid.startswith(TRI_STRIP_SHAPE) and len(body) >= 68:
                bb = struct.unpack_from("<6f", body, 16)
                area = struct.unpack_from("<f", body, 64)[0]
                out.append(DisplayShape(seg, [round(v * 1e3, 3) for v in bb], round(area * 1e6, 2)))
            p += 4 + length
    return out
