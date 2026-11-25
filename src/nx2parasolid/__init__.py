"""nx2parasolid - exact B-rep geometry out of Siemens NX .prt files, without NX.

Quick use::

    from nx2parasolid import convert_prt
    result = convert_prt("bracket.prt", "out/bracket")
    print([f.file for f in result.files])

See the README for the command-line tool and how it works.
"""
__version__ = "0.1.0"

from .container import PrtFile  # noqa: E402
from .core import PartResult, convert_prt  # noqa: E402
from .errors import (  # noqa: E402
    NotAnNXFile,
    Nx2ParasolidError,
    PossiblyEncrypted,
    SchemaNotFound,
    SolidWorksError,
    XTDecodeError,
)
from .schema import SchemaStore  # noqa: E402
from .streams import extract_streams  # noqa: E402
from .xt import read_transmit, write_transmit  # noqa: E402

__all__ = [
    "__version__", "PrtFile", "PartResult", "convert_prt", "SchemaStore", "extract_streams",
    "read_transmit", "write_transmit", "Nx2ParasolidError", "NotAnNXFile", "PossiblyEncrypted",
    "SchemaNotFound", "SolidWorksError", "XTDecodeError",
]
