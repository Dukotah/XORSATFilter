"""High-level wrapper around the cffi extension ``pyxorsat._xorsat``."""

from __future__ import annotations

import os
import tempfile
from typing import Iterable, Optional, Union

from ._xorsat import ffi, lib

Key = Union[bytes, bytearray, memoryview, str]

PARAMETERS = {
    "efficient": lib.XORSATFilterEfficientParameters,
    "paper": lib.XORSATFilterPaperParameters,
    "fast": lib.XORSATFilterFastParameters,
    "dw-efficient": lib.XORSATFilterDWEfficientParameters,
    "dw-paper": lib.XORSATFilterDWPaperParameters,
    "dw-fast": lib.XORSATFilterDWFastParameters,
}
"""Named parameter sets shipped with the C library (see ``src/xorsat_blocks.c``).
The ``dw-*`` sets use 2 literals per row (Dietzfelbinger-Walzer hashing)."""


class XORSATError(RuntimeError):
    pass


def _key(k: Key) -> bytes:
    if isinstance(k, str):
        return k.encode("utf-8")
    if isinstance(k, (bytes, bytearray, memoryview)):
        return bytes(k)
    raise TypeError(f"keys must be bytes-like or str, not {type(k).__name__}")


def _params(params) -> "ffi.CData":
    if isinstance(params, str):
        try:
            return PARAMETERS[params]
        except KeyError:
            raise ValueError(f"unknown parameter set {params!r}; one of {sorted(PARAMETERS)}") from None
    if isinstance(params, dict):
        p = ffi.new("XORSATFilterParameters *")
        p.nLitsPerRow = params["lits_per_row"]
        p.nSolutions = params["solutions"]
        p.nEltsPerBlock = params["elts_per_block"]
        p.fEfficiency = params.get("efficiency", 1.0)
        return p[0]
    raise TypeError("params must be a parameter-set name or a dict")


def _packed(keys, key_bytes: Optional[int]):
    """Return (buffer, key_bytes, n) for either a packed bytes object or an
    iterable of equal-length keys."""
    if isinstance(keys, (bytes, bytearray, memoryview)):
        if not key_bytes:
            raise ValueError("key_bytes is required when passing a packed buffer")
        buf = bytes(keys)
        if len(buf) % key_bytes:
            raise ValueError("packed buffer length is not a multiple of key_bytes")
        return buf, key_bytes, len(buf) // key_bytes
    items = [_key(k) for k in keys]
    if not items:
        return b"", key_bytes or 1, 0
    w = len(items[0])
    if any(len(x) != w for x in items):
        raise ValueError("add_many/query_many need equal-length keys; use add()/query() otherwise")
    return b"".join(items), w, len(items)


class Builder:
    """Collects elements (and optional fixed-width metadata) before finalizing."""

    def __init__(self, expected: int = 0, meta_bytes: int = 0):
        if not 0 <= expected < 2**32:
            raise ValueError("expected must fit in uint32")
        self.meta_bytes = int(meta_bytes)
        self._b = lib.XORSATFilterBuilderAlloc(expected, self.meta_bytes)
        if self._b == ffi.NULL:
            raise MemoryError("XORSATFilterBuilderAlloc failed")
        self.n_elements = 0
        self.n_absences = 0

    def _check(self):
        if self._b is None:
            raise XORSATError("builder is closed")

    def _meta(self, meta):
        if self.meta_bytes == 0:
            if meta is not None:
                raise ValueError("this builder has meta_bytes=0")
            return ffi.NULL
        if meta is None or len(meta) != self.meta_bytes:
            raise ValueError(f"metadata must be exactly {self.meta_bytes} bytes")
        return bytes(meta)

    def add(self, key: Key, meta: Optional[bytes] = None) -> None:
        self._check()
        k = _key(key)
        if lib.XORSATFilterBuilderAddElement(self._b, k, len(k), self._meta(meta)) != 0:
            raise XORSATError("XORSATFilterBuilderAddElement failed")
        self.n_elements += 1

    def add_absence(self, key: Key) -> None:
        """Force the finished filter to answer False for ``key``."""
        self._check()
        k = _key(key)
        if lib.XORSATFilterBuilderAddAbsence(self._b, k, len(k)) != 0:
            raise XORSATError("XORSATFilterBuilderAddAbsence failed")
        self.n_absences += 1

    def add_many(self, keys, key_bytes: Optional[int] = None, metas: Optional[bytes] = None) -> None:
        """Add equal-length keys in one C call. ``keys`` is an iterable of keys or a
        packed buffer of ``n * key_bytes`` bytes; ``metas`` is a packed buffer of
        ``n * meta_bytes`` bytes."""
        self._check()
        buf, w, n = _packed(keys, key_bytes)
        if n == 0:
            return
        if self.meta_bytes:
            if metas is None or len(metas) != n * self.meta_bytes:
                raise ValueError(f"metas must be {n} * {self.meta_bytes} bytes")
            m = bytes(metas)
        else:
            if metas is not None:
                raise ValueError("this builder has meta_bytes=0")
            m = ffi.NULL
        if lib.pyxs_add_many(self._b, buf, w, n, m, self.meta_bytes, 0) != 0:
            raise XORSATError("adding elements failed")
        self.n_elements += n

    def add_absences_many(self, keys, key_bytes: Optional[int] = None) -> None:
        self._check()
        buf, w, n = _packed(keys, key_bytes)
        if n and lib.pyxs_add_many(self._b, buf, w, n, ffi.NULL, 0, 1) != 0:
            raise XORSATError("adding absences failed")
        self.n_absences += n

    def finalize(self, params="dw-paper", threads: Optional[int] = None) -> "Filter":
        """Solve the XORSAT systems and return a queryable :class:`Filter`.
        The builder stays usable only for :meth:`close`."""
        self._check()
        if self.n_elements == 0:
            raise XORSATError("cannot finalize an empty builder")
        q = lib.XORSATFilterBuilderFinalize(self._b, _params(params), threads or os.cpu_count() or 1)
        if q == ffi.NULL:
            raise XORSATError("XORSATFilterBuilderFinalize failed (try a different parameter set)")
        return Filter(q, n_elements=self.n_elements)

    def close(self) -> None:
        if getattr(self, "_b", None) is not None:
            lib.XORSATFilterBuilderFree(self._b)
            self._b = None

    __del__ = close

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Filter:
    """A finalized (immutable) k-XORSAT filter or dictionary."""

    def __init__(self, querier, n_elements: Optional[int] = None):
        self._q = querier
        self.n_elements = n_elements
        self.meta_bytes = int(lib.pyxs_meta_bytes(querier))

    def _check(self):
        if self._q is None:
            raise XORSATError("filter is closed")

    # -- queries -----------------------------------------------------------
    def query(self, key: Key) -> bool:
        self._check()
        k = _key(key)
        return lib.XORSATFilterQuery(self._q, k, len(k)) == 1

    __contains__ = query

    def query_many(self, keys, key_bytes: Optional[int] = None) -> bytes:
        """Query equal-length keys in one C call; returns one 0/1 byte per key."""
        self._check()
        buf, w, n = _packed(keys, key_bytes)
        out = ffi.new("uint8_t[]", max(n, 1))
        lib.pyxs_query_many(self._q, buf, w, n, out)
        return bytes(ffi.buffer(out, n))

    def count_many(self, keys, key_bytes: Optional[int] = None) -> int:
        """Number of keys answered True, without materialising the answers."""
        self._check()
        buf, w, n = _packed(keys, key_bytes)
        return int(lib.pyxs_query_many(self._q, buf, w, n, ffi.NULL))

    def get(self, key: Key) -> bytes:
        """Stored metadata for ``key``. For keys never added the bytes are
        arbitrary; check ``key in filter`` first."""
        self._check()
        if self.meta_bytes == 0:
            raise XORSATError("filter was built with meta_bytes=0")
        k = _key(key)
        p = lib.XORSATFilterRetrieveMetadata(self._q, k, len(k))
        if p == ffi.NULL:
            raise MemoryError("XORSATFilterRetrieveMetadata failed")
        try:
            return bytes(ffi.buffer(p, self.meta_bytes))
        finally:
            lib.free(p)

    def measure_fpr(self, n: int = 1_000_000, seed: int = 0x5EED) -> float:
        """Empirical false-positive rate over ``n`` random 17-byte keys. Do not
        use if your real elements are exactly 17 bytes long."""
        self._check()
        return int(lib.pyxs_count_random_hits(self._q, n, seed)) / n

    # -- sizes -------------------------------------------------------------
    @property
    def filter_bits(self) -> int:
        return int(lib.XORSATFilterSize(self._q))

    @property
    def metadata_bits(self) -> int:
        return int(lib.XORSATMetaDataSize(self._q))

    @property
    def ancillary_bits(self) -> int:
        return int(lib.XORSATAncillarySize(self._q))

    @property
    def nbytes(self) -> int:
        """Serialized size in bytes (filter + metadata + block offsets + header)."""
        return (self.filter_bits + self.metadata_bits + self.ancillary_bits + 7) // 8

    @property
    def bits_per_element(self) -> Optional[float]:
        if not self.n_elements:
            return None
        return self.filter_bits / self.n_elements

    # -- serialization -----------------------------------------------------
    def save(self, path: Union[str, os.PathLike]) -> None:
        self._check()
        if lib.pyxs_serialize_path(self._q, os.fsencode(path)) != 0:
            raise XORSATError(f"XORSATFilterSerialize failed for {path}")

    @classmethod
    def load(cls, path: Union[str, os.PathLike]) -> "Filter":
        """Load a serialized filter. The C library mmap()s the file read-only."""
        q = lib.pyxs_deserialize_path(os.fsencode(path))
        if q == ffi.NULL:
            raise XORSATError(f"XORSATFilterDeserialize failed for {path}")
        return cls(q)

    def to_bytes(self) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "filter.xor")
            self.save(p)
            with open(p, "rb") as f:
                return f.read()

    @classmethod
    def from_bytes(cls, data: bytes) -> "Filter":
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "filter.xor")
            with open(p, "wb") as f:
                f.write(data)
            return cls.load(p)  # mapping survives the file being unlinked

    def close(self) -> None:
        if getattr(self, "_q", None) is not None:
            lib.XORSATFilterQuerierFree(self._q)
            self._q = None

    __del__ = close

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
