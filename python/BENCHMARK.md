Benchmark
=========

Produced by `python benchmarks/bench.py` on 2026-11-17: Intel Core Ultra 7
258V, WSL2 Ubuntu (6 vCPUs visible), Python 3.12.13, pyxorsat 0.1.0 built by
`uv build --wheel` (no `-march=native`), rbloom 1.5.4, pyfusefilter 1.3.3.
The machine was shared with other build jobs during the run, so treat the
throughput columns as +/- 30 %; sizes and FPRs are deterministic.

N = 1,000,000 distinct random 8-byte keys; FPR measured on 1,000,000 other
random keys.

| structure | build (s) | bits/key | size | measured FPR | `in` loop (M q/s) | batch (M q/s) |
|---|---:|---:|---:|---:|---:|---:|
| pyxorsat k-XORSAT (2^-7, 6 threads) | 5.55 | 7.08 | 0.84 MiB | 0.7827 % | 1.16 | 9.1 |
| pyxorsat k-XORSAT (2^-7, 1 thread) | 16.22 | 7.08 | 0.84 MiB | 0.7808 % | 1.12 | 4.7 |
| rbloom Bloom (2^-7) | 0.14 | 10.10 | 1.20 MiB | 0.7918 % | 3.49 |  |
| pyfusefilter Xor8 | 1.06 | 9.84 | 1.17 MiB | 0.3943 % | 0.79 |  |
| pyfusefilter Fuse8 | 0.56 | 9.04 | 1.08 MiB | 0.3859 % | 1.08 |  |
| pyxorsat k-XORSAT (2^-16, 6 threads) | 4.91 | 16.18 | 1.93 MiB | 0.0014 % | 1.14 | 6.4 |
| pyxorsat k-XORSAT (2^-16, 1 thread) | 16.25 | 16.18 | 1.93 MiB | 0.0012 % | 1.26 | 7.9 |
| rbloom Bloom (2^-16) | 0.15 | 23.08 | 2.75 MiB | 0.0011 % | 3.85 |  |
| pyfusefilter Xor16 | 0.81 | 19.68 | 2.35 MiB | 0.0012 % | 1.09 |  |
| pyfusefilter Fuse16 | 0.62 | 18.09 | 2.16 MiB | 0.0014 % | 1.05 |  |
| Python set (exact) | 0.12 | 596.44 | 71.10 MiB | 0 | 4.05 |  |

The 2^-7 row uses `dw-paper`; the 2^-16 row uses
`{"lits_per_row": 2, "solutions": 16, "elts_per_block": 3072}`.

Reading it:

* **Size** is where k-XORSAT wins: 7.08 bits/key at FPR 2^-7 (the
  information-theoretic floor is 7.00) versus 9.04 for a binary fuse filter
  and 10.10 for a Bloom filter. At 2^-16 it is 16.18 versus 18.09 / 23.08.
  Xor8/Fuse8 have a fixed FPR of about 2^-8, so compare them on bits per key
  for *their* FPR (8 bits is their floor).
* **Build** is where it loses: seconds instead of tenths of a second, because
  it solves a linear system per block. Threads help (16.2 s -> 5.5 s on 6).
* **Per-key queries from Python** are bounded by the cffi call; rbloom's
  Rust extension is about 3x faster per call. With `query_many`/`count_many`
  (one C call for a packed buffer of keys) pyxorsat answers 5-9 M keys/s.
  Packing the keys in Python first eats most of that: in the Liber Primus
  example, 1.21 M q/s including packing versus 0.78 for the `in` loop and
  10.94 on pre-packed keys. Batching pays most when keys already sit in a
  buffer (numpy array, file, network).
* pyfusefilter hashes keys with Python's `hash()`, which is salted per process
  for bytes, so its filters are not portable between processes; XORSAT
  filters serialize to a file (`save`/`load`, mmap()ed on load).

Liber Primus example
--------------------

`python examples/liber_primus_swept_seeds.py` (same box, 0:36 wall, 483 MB
peak RSS) indexes which (generator configuration, seed) pairs the Liber
Primus seed sweeps have already covered. Its honest result is that for
contiguous sweep ranges a sorted interval list (144 bytes) beats every other
structure; the k-XORSAT index with unswept blocks added as absences is exact
over the block universe but only matches a bitmap (392.8 KiB vs 384.0 KiB).
The filter earns its place on the scattered "seed zoo" set: 4,522
(config, seed) points in 11.4 KiB at a measured FPR of 5.0e-7, versus 39.7 KiB
for packed (config id, uint64 seed) pairs. Full output is in LANE-REPORT.md
on the `lane/python-bindings` branch.
