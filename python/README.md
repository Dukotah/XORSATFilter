pyxorsat
========

cffi bindings for the XORSATFilter C library in the parent directory.
The extension is compiled from `../src` and the `../lib` submodules, so
initialise the submodules first:

```
git submodule update --init --recursive
cd python
uv build --wheel -p 3.12   # dist/pyxorsat-0.1.0-cp312-cp312-linux_x86_64.whl
uv venv -p 3.12 && . .venv/bin/activate
uv pip install dist/*.whl pytest
pytest                     # 18 tests
```

Unlike the top-level Makefile, the extension is built without
`-march=native` (so the wheel runs on other machines) and without
`-ffast-math` (which would change floating-point behaviour for the whole
Python process). Build-progress output is disabled.

Linux only: the C library uses `mmap` and pthreads.


USE
===

```python
from pyxorsat import Builder, Filter

b = Builder(expected=1_000_000)          # meta_bytes=0 -> filter
b.add(b"alice")
b.add_many(keys)                         # equal-length keys, one C call
b.add_absence(b"mallory")                # forced to answer False
f = b.finalize("dw-paper", threads=8)    # or a dict, see below
b.close()

b"alice" in f                            # True
f.query_many(probe)                      # bytes of 0/1, one C call
f.count_many(probe)                      # number of positives
f.measure_fpr(1_000_000)                 # empirical false-positive rate
f.bits_per_element, f.nbytes

f.save("f.xor"); g = Filter.load("f.xor")    # same format as XORSATFilterSerialize
data = f.to_bytes(); g = Filter.from_bytes(data)
```

With `Builder(meta_bytes=N)` the structure is a static dictionary and
`f.get(key)` returns the `N` bytes stored for `key` (arbitrary bytes for
keys that were never added).

Parameter sets: `efficient`, `paper`, `fast`, `dw-efficient`, `dw-paper`,
`dw-fast` (the C library's `XORSATFilter*Parameters`), or a dict
`{"lits_per_row": 2, "solutions": 12, "elts_per_block": 1024, "efficiency": 1.0}`.
The false-positive rate is about `2**-solutions`; `solutions` must be at most 32.

See `BENCHMARK.md` for measurements against Bloom and xor/fuse filters and
`examples/liber_primus_swept_seeds.py` for a worked example.
