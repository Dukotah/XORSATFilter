"""Python bindings for XORSATFilter.

k-XORSAT filters are *static* approximate set-membership structures: you add every
element up front, call :meth:`Builder.finalize`, and then only query. In exchange
they come within a few percent of the information-theoretic minimum size
(about ``nSolutions`` bits per element for a false-positive rate of
``2**-nSolutions``). With ``meta_bytes > 0`` the same structure is a static
dictionary that returns a fixed-width value per key.

Example::

    from pyxorsat import Builder
    b = Builder()
    for key in [b"alice", b"bob"]:
        b.add(key)
    f = b.finalize()
    assert b"alice" in f
"""

from .core import (
    PARAMETERS,
    Builder,
    Filter,
    XORSATError,
)

__all__ = ["PARAMETERS", "Builder", "Filter", "XORSATError"]
__version__ = "0.1.0"
