"""Read-only access to the stream container inside an NX ``.prt`` file.

An NX part file is a simple archive (it starts with ``SPLMSSTR``): a directory of named streams
followed by their data. Each directory entry is::

    uint32 name_length (little-endian)
    name bytes          ("/Root/...")
    uint64 offset       (little-endian, from the start of the file)
    uint64 length

The streams nx2parasolid uses:

* ``/Root/UG_PART/UG_PART``    - the model, including zlib-compressed Parasolid transmits
* ``/Root/FastLoad/JT``        - NX's lightweight display data (JT), used to tell which bodies
                                 NX actually shows for the part
* ``/Root/FastLoad/Structure`` - assembly structure (component part names)
* ``/Root/images/preview``     - a JPEG thumbnail
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from .errors import NotAnNXFile

UG_PART = "/Root/UG_PART/UG_PART"
FASTLOAD_JT = "/Root/FastLoad/JT"
FASTLOAD_STRUCTURE = "/Root/FastLoad/Structure"
PREVIEW = "/Root/images/preview"

_MAX_NAME = 512


@dataclass(frozen=True)
class Entry:
    """One stream in the container directory."""

    name: str
    offset: int
    length: int


def read_directory(data: bytes) -> List[Entry]:
    """Parse the stream directory. Raises :class:`NotAnNXFile` if there is none."""
    pos = data.find(b"/Root/")
    if pos < 4:
        raise NotAnNXFile("no NX stream directory found (not an NX .prt file?)")
    pos -= 4
    entries: List[Entry] = []
    while pos + 4 <= len(data):
        (n,) = struct.unpack_from("<I", data, pos)
        if n == 0 or n > _MAX_NAME or pos + 4 + n + 16 > len(data):
            break
        name = data[pos + 4: pos + 4 + n]
        if not name.startswith(b"/Root/"):
            break
        offset, length = struct.unpack_from("<QQ", data, pos + 4 + n)
        pos += 4 + n + 16
        if offset + length > len(data):
            continue   # directory-only entries (e.g. "/Root/") carry no stream data
        entries.append(Entry(name.decode("latin1"), offset, length))
    if not entries:
        raise NotAnNXFile("empty NX stream directory")
    return entries


class PrtFile:
    """An NX ``.prt`` file opened read-only. The file on disk is never written."""

    def __init__(self, data: bytes, name: str = "<bytes>") -> None:
        self.data = data
        self.name = name
        self.entries = read_directory(data)
        self._by_name: Dict[str, Entry] = {e.name: e for e in self.entries}

    @classmethod
    def open(cls, path: Union[str, Path]) -> PrtFile:
        p = Path(path)
        return cls(p.read_bytes(), p.stem)

    @property
    def magic(self) -> bytes:
        return self.data[:8]

    def names(self) -> List[str]:
        return [e.name for e in self.entries]

    def has(self, name: str) -> bool:
        return name in self._by_name and self._by_name[name].length > 0

    def stream(self, name: str) -> bytes:
        """Raw bytes of a stream (``b""`` if it is absent or empty)."""
        e: Optional[Entry] = self._by_name.get(name)
        return b"" if e is None else self.data[e.offset: e.offset + e.length]
