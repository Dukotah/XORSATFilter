"""Compare pyxorsat against other Python-reachable static/dynamic filters.

    python benchmarks/bench.py [--n 1000000]

Every structure gets the same N distinct random 8-byte keys. The false-positive
rate is measured on N other random 8-byte keys that are not members. Query
throughput is a plain Python loop of ``key in f`` over the members, so it is
dominated by interpreter + FFI call overhead; ``query_many`` is pyxorsat's
one-C-call batch path. Optional competitors are skipped if not installed:
rbloom (Rust Bloom filter), pyfusefilter (Xor8/Xor16/Fuse8/Fuse16, the
Lemire et al. xor and binary-fuse filters).
"""

import argparse
import gc
import math
import os
import platform
import random
import struct
import sys
import time

from pyxorsat import Builder


def make_keys(n, seed):
    rng = random.Random(seed)
    s = set()
    while len(s) < 2 * n:
        s.add(rng.getrandbits(64))
    vals = list(s)
    rng.shuffle(vals)
    pack = struct.Struct("<Q").pack
    keys = [pack(v) for v in vals]
    return keys[:n], keys[n:]


def timed(fn):
    gc.collect()
    t = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t


def loop_rate(f, keys):
    def run():
        c = 0
        for k in keys:
            if k in f:
                c += 1
        return c
    c, dt = timed(run)
    assert c == len(keys), f"false negatives: {len(keys) - c}"
    return len(keys) / dt


def fpr(f, probe):
    return sum(1 for k in probe if k in f) / len(probe)


def row(name, build_s, nbytes, n, fp, qps, batch_qps=None):
    return {
        "name": name, "build_s": build_s, "bits": 8 * nbytes / n, "bytes": nbytes,
        "fpr": fp, "qps": qps, "batch_qps": batch_qps,
    }


def bench_xorsat(name, params, keys, probe, threads):
    def build():
        b = Builder(expected=len(keys))
        b.add_many(keys)
        f = b.finalize(params, threads=threads)
        b.close()
        return f
    f, dt = timed(build)
    packed = b"".join(keys)
    _, bdt = timed(lambda: f.count_many(packed, key_bytes=8))
    r = row(name, dt, f.nbytes, len(keys), fpr(f, probe), loop_rate(f, keys), len(keys) / bdt)
    f.close()
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1_000_000)
    ap.add_argument("--threads", type=int, default=os.cpu_count())
    args = ap.parse_args()
    n = args.n

    keys, probe = make_keys(n, 1)
    rows = []

    for sol, label in [(7, "2^-7"), (16, "2^-16")]:
        params = "dw-paper" if sol == 7 else {"lits_per_row": 2, "solutions": 16, "elts_per_block": 3072}
        rows.append(bench_xorsat(f"pyxorsat k-XORSAT ({label}, {args.threads} threads)", params, keys, probe, args.threads))
        rows.append(bench_xorsat(f"pyxorsat k-XORSAT ({label}, 1 thread)", params, keys, probe, 1))

        try:
            import rbloom
            f, dt = timed(lambda: (lambda b: (b.update(keys), b)[1])(rbloom.Bloom(n, 2.0 ** -sol)))
            rows.append(row(f"rbloom Bloom ({label})", dt, f.size_in_bits / 8, n, fpr(f, probe), loop_rate(f, keys)))
        except ImportError:
            pass

        try:
            import pyfusefilter
            for cls in ([pyfusefilter.Xor8, pyfusefilter.Fuse8] if sol == 7 else [pyfusefilter.Xor16, pyfusefilter.Fuse16]):
                f, dt = timed(lambda: cls(keys))
                rows.append(row(f"pyfusefilter {cls.__name__}", dt, f.size_in_bytes(), n, fpr(f, probe), loop_rate(f, keys)))
        except ImportError:
            pass

    s, dt = timed(lambda: set(keys))
    set_bytes = sys.getsizeof(s) + sum(sys.getsizeof(k) for k in keys)
    rows.append(row("Python set (exact)", dt, set_bytes, n, fpr(s, probe), loop_rate(s, keys)))

    print(f"N = {n:,} distinct 8-byte keys; {len(probe):,} non-member probes")
    print(f"{platform.processor() or platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}")
    print()
    print("| structure | build (s) | bits/key | size | measured FPR | `in` loop (M q/s) | batch (M q/s) |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        size = f"{r['bytes'] / 2**20:.2f} MiB"
        fp = f"{r['fpr'] * 100:.4f} %" if r["fpr"] else "0"
        b = f"{r['batch_qps'] / 1e6:.1f}" if r["batch_qps"] else ""
        print(f"| {r['name']} | {r['build_s']:.2f} | {r['bits']:.2f} | {size} | {fp} | {r['qps'] / 1e6:.2f} | {b} |")
    print()
    print("Information-theoretic minimum for FPR 2^-k is k bits/key "
          f"(7 and 16 here); Bloom needs k*{1 / math.log(2):.2f}.")


if __name__ == "__main__":
    main()
