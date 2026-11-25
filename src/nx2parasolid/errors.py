"""Exceptions raised by nx2parasolid."""


class Nx2ParasolidError(Exception):
    """Base class for every error this package raises on purpose."""


class NotAnNXFile(Nx2ParasolidError):
    """The input does not contain an NX (SPLMSSTR) stream directory."""


class PossiblyEncrypted(Nx2ParasolidError):
    """Data after a Parasolid header is not plain zlib. The file may be encrypted or
    licence-protected; nx2parasolid stops here and does not try to work around it."""


class SchemaNotFound(Nx2ParasolidError):
    """No Parasolid schema file for the schema key a transmit refers to."""


class XTDecodeError(Nx2ParasolidError):
    """Parasolid binary data could not be decoded node by node."""


class SolidWorksError(Nx2ParasolidError):
    """The optional SolidWorks step failed or is not available."""
