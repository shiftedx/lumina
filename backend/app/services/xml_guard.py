"""Declaration preflight for bounded untrusted XML, independent of its encoding."""
from __future__ import annotations

from xml.parsers import expat


class _DeclarationFound(Exception):
    pass


def _reject_declaration(*_args: object) -> None:
    raise _DeclarationFound


def declares_dtd(data: bytes) -> bool:
    """Stop at a DTD/entity declaration before parsing its contents or expanding entities.

    Callers bound input bytes first and keep their ordinary XML parse/error handling.
    This follows the NFO parser's declaration preflight without loading its services.
    """
    probe = expat.ParserCreate()
    probe.StartDoctypeDeclHandler = probe.EntityDeclHandler = _reject_declaration
    try:
        probe.Parse(data, True)
    except (_DeclarationFound, ValueError):
        return True  # Unknown/unsupported encoding fails closed too.
    except expat.ExpatError:
        pass  # The caller's ordinary parser rejects malformed XML.
    return False
