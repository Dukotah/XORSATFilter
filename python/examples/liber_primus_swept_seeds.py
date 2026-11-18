"""Has this (generator configuration, seed) already been swept?

A worked example of pyxorsat on real bookkeeping data: the Liber Primus seed
sweeps recorded in the owner's ``cicada3301/liber-primus`` repository.

What it is
----------
A *membership index of swept work*. Someone resuming the Py2.7-MT19937 seed
sweep (or adding a new generator) can ask "was (config, seed) already scanned?"
and skip it, without shipping the sweep logs. Inputs, all read-only:

* ``volunteer/shards.json`` - the S2 lane definition and the volunteer shard table
  (``done`` shards count as swept; ``TEST-`` shards never do);
* the engine ``STATUS.json`` that shards.json cites as the S2 prior
  (per-worker ``[band_start, cursor)`` is what the engine actually finished;
  ``lanes_completed`` lists lanes swept over the full ``[from, to)``);
* ``LEDGER.json`` rows whose ``coverage`` field is a machine-readable dict with a
  ``seed_range_this_chunk`` (today: R25-COMPUTE-TAIL-CHUNK1);
* ``analysis/round26/C/seeds.json`` - the 323 corpus-semantic seeds that ledger
  row R26-C-SEMANTIC-SEED-ZOO says were fully swept through 7 generators x 2
  decoders at offset 0 (the generator/decoder names are copied from that row).

Range coverage is indexed at 4096-seed block granularity (a block counts as
swept only if *every* seed in it was swept, so partial edge blocks are reported
unswept - the safe direction). The sparse seed-zoo set is indexed per seed.

What it is not
--------------
* Not a record of results, scores or candidates - only "this was scanned".
* Not a statement about instrument power. Per LEDGER rows R36/R38/R42, the S2
  cursor coverage is *mixed instrument* (most of it was gated by the Round 19
  panel and never re-scored under panel-R36). "Swept" here means "the engine
  cursor passed it", nothing more.
* Not exhaustive: 310 ledger rows have a coverage field, but almost all of them
  are prose. Only the sources above are machine-readable; everything else (e.g.
  the 45,975 R21 prior words) is not in the index.
* A plain filter answers "maybe swept" for ~1/128 of never-swept keys. For this
  use a false positive means a seed is silently *skipped*, which is the bad
  direction, so the range index below is built with every unswept block added
  as an *absence* - that makes it exact over the whole 2^32 block universe.
* For contiguous ranges a filter is the wrong tool: a sorted interval list is
  exact, per-seed, and a few hundred bytes. The script prints that comparison
  rather than hiding it. XORSAT only pays off for scattered sets (the seed zoo),
  and only beats a bitmap when the set's density is below about 1/k.

Run (from python/, after installing pyxorsat)::

    python examples/liber_primus_swept_seeds.py --repo /path/to/cicada3301/liber-primus
"""

import argparse
import bisect
import json
import os
import random
import struct
import time
from pathlib import Path

from pyxorsat import Builder

BLOCK_BITS = 12  # 4096 seeds per block
SPACE = 1 << 32
NBLOCKS = SPACE >> BLOCK_BITS

# Lane configurations, abbreviated from shards.json / LEDGER.json wording.
S1 = "py27-mt19937/init_by_array/random29/keyskip2-pair/o0"
S2 = "py27-mt19937/init_by_array/random29/keyskip1-exact/o0"

# From LEDGER row R26-C-SEMANTIC-SEED-ZOO coverage: 7 generators x 2 decoders, offset 0.
ZOO_GENERATORS = ["gen_py27/random29", "gen_py27/grb5_mod", "gen_py27/grb5_rej",
                  "gen_perl/glibc_g_r29", "ciphers/prime", "ciphers/totient", "ciphers/prime_totient"]
ZOO_DECODERS = ["keyskip1-exact", "keyskip2-pair"]


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(r) for r in out]


def collect(repo: Path):
    """Return ({config: [(from, to), ...]}, [(config, seed), ...], provenance lines)."""
    ranges, points, notes = {}, [], []
    shards = load_json(repo / "volunteer" / "shards.json")
    lane = shards["lanes"]["S2"]
    status_path = repo / lane["prior"]["source"]  # path is relative to liber-primus/
    status = load_json(status_path)

    s2 = [(w["band_start"], w["cursor"]) for w in status["workers"]]
    done = [(s["from"], s["to"]) for s in shards["shards"]
            if s["status"] == "done" and not s["id"].startswith("TEST")]
    ranges[S2] = merge(s2 + done)
    notes.append(f"S2: {len(s2)} engine worker bands from {status_path.relative_to(repo)}, "
                 f"{len(done)} done volunteer shards")
    for lane_id in status.get("lanes_completed", []):
        if lane_id == "S1":
            ranges[S1] = [(status["from"], status["to"])]
            notes.append(f"S1: lanes_completed in STATUS.json -> [{status['from']}, {status['to']})")

    ledger = load_json(repo / "LEDGER.json")
    n_cov = 0
    for row in ledger["entries"]:
        cov = row.get("coverage")
        n_cov += bool(cov)
        if isinstance(cov, dict) and "seed_range_this_chunk" in cov:
            cfg = f"ledger:{row['id']}"
            rs = [tuple(cov["seed_range_this_chunk"])]
            base = cov.get("excluded_already_swept", {}).get("dense_baseline_contiguous")
            if base:
                rs.append(tuple(base))
            ranges[cfg] = merge(rs)
            notes.append(f"{cfg}: {len(rs)} ranges from the ledger coverage dict")
    notes.append(f"LEDGER.json: {len(ledger['entries'])} rows, {n_cov} with a coverage field, "
                 f"{sum(1 for k in ranges if k.startswith('ledger:'))} machine-readable seed ranges")

    zoo = load_json(repo / "analysis" / "round26" / "C" / "seeds.json")
    for s in zoo["seeds"]:
        for g in ZOO_GENERATORS:
            for d in ZOO_DECODERS:
                points.append((f"{g}/{d}/o0", s["value"]))
    notes.append(f"seed zoo: {zoo['n_total']} seeds x {len(ZOO_GENERATORS)} generators x "
                 f"{len(ZOO_DECODERS)} decoders = {len(points)} (config, seed) points")
    return ranges, points, notes


def block_key(cfg: str, block: int) -> bytes:
    return cfg.encode() + b"\x00B" + struct.pack("<I", block)


def point_key(cfg: str, seed) -> bytes:
    s = struct.pack("<Q", seed) if isinstance(seed, int) else b"s:" + str(seed).encode()
    return cfg.encode() + b"\x00P" + s


def full_blocks(rs):
    """Indices of blocks lying entirely inside the merged ranges."""
    out = []
    for a, b in rs:
        first = (a + (1 << BLOCK_BITS) - 1) >> BLOCK_BITS
        last = b >> BLOCK_BITS  # exclusive
        out.extend(range(first, last))
    return out


def fmt(n):
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if n < 1024 or unit == "GiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    default_repo = os.environ.get("LIBER_PRIMUS", "/mnt/c/Users/dukot/projects/cicada3301/liber-primus")
    ap.add_argument("--repo", type=Path, default=Path(default_repo))
    ap.add_argument("--probes", type=int, default=1_000_000)
    args = ap.parse_args()

    ranges, points, notes = collect(args.repo)
    print("Sources")
    for n in notes:
        print("  -", n)
    print()

    print("Range coverage (per config)")
    blocks = {}
    for cfg, rs in ranges.items():
        seeds = sum(b - a for a, b in rs)
        blocks[cfg] = full_blocks(rs)
        print(f"  {cfg}\n      {len(rs)} intervals, {seeds:,} seeds = {100 * seeds / SPACE:.3f} % of 2^32, "
              f"{len(blocks[cfg]):,} full 4096-seed blocks")
    print()

    n_members = sum(len(v) for v in blocks.values())
    universe = len(blocks) * NBLOCKS

    # (a) plain filter over swept blocks: smallest, but ~1/128 false "swept".
    t = time.perf_counter()
    b = Builder(expected=n_members)
    for cfg, bl in blocks.items():
        b.add_many([block_key(cfg, i) for i in bl])
    plain = b.finalize("dw-paper")
    b.close()
    t_plain = time.perf_counter() - t

    # (b) exact over the block universe: every unswept block is an absence.
    t = time.perf_counter()
    b = Builder(expected=universe)
    for cfg, bl in blocks.items():
        swept = set(bl)
        b.add_many([block_key(cfg, i) for i in bl])
        b.add_absences_many([block_key(cfg, i) for i in range(NBLOCKS) if i not in swept])
    exact = b.finalize({"lits_per_row": 2, "solutions": 1, "elts_per_block": 3072})
    b.close()
    t_exact = time.perf_counter() - t

    # Verify both against ground truth over the full block universe.
    fp_plain = fp_exact = fn = 0
    for cfg, bl in blocks.items():
        swept = bytearray(NBLOCKS)
        for i in bl:
            swept[i] = 1
        keys = b"".join(block_key(cfg, i) for i in range(NBLOCKS))
        w = len(block_key(cfg, 0))
        a_plain = plain.query_many(keys, key_bytes=w)
        a_exact = exact.query_many(keys, key_bytes=w)
        for i in range(NBLOCKS):
            if swept[i]:
                fn += (not a_plain[i]) + (not a_exact[i])
            else:
                fp_plain += a_plain[i]
                fp_exact += a_exact[i]
    n_unswept = universe - n_members
    assert fn == 0, "false negative"

    intervals_bytes = 16 * sum(len(rs) for rs in ranges.values())
    print(f"Index over {len(blocks)} configs x 2^20 blocks ({n_members:,} swept, {n_unswept:,} unswept)")
    print("| structure | size | exact? | wrong 'swept' answers on unswept blocks |")
    print("|---|---:|---|---:|")
    print(f"| sorted interval list (2 x uint64 per interval, per-seed resolution) | {fmt(intervals_bytes)} | yes | 0 |")
    print(f"| bitmap, 1 bit per block per config | {fmt(universe / 8)} | yes | 0 |")
    print(f"| sorted uint32 list of swept blocks | {fmt(4 * n_members)} | yes | 0 |")
    print(f"| XORSAT filter of swept blocks (dw-paper, 2^-7) | {fmt(plain.nbytes)} | no | "
          f"{fp_plain:,} of {n_unswept:,} ({100 * fp_plain / max(n_unswept, 1):.2f} %) |")
    print(f"| XORSAT, swept + unswept-as-absences (1 solution) | {fmt(exact.nbytes)} | yes, in-universe | "
          f"{fp_exact:,} |")
    print(f"  build: plain {t_plain:.1f} s, exact {t_exact:.1f} s (includes building Python key lists)")
    print()

    # Sparse part: the semantic seed zoo, per seed.
    t = time.perf_counter()
    b = Builder(expected=len(points))
    for cfg, seed in points:
        b.add(point_key(cfg, seed))
    zoo = b.finalize({"lits_per_row": 2, "solutions": 20, "elts_per_block": 1024})
    b.close()
    t_zoo = time.perf_counter() - t
    assert all(point_key(c, s) in zoo for c, s in points)
    fpr_zoo = zoo.measure_fpr(2_000_000)
    raw = sum(len(point_key(c, s)) for c, s in points)
    print(f"Seed zoo: {len(points):,} (config, seed) points, scattered over 2^32 per config")
    print("| structure | size |")
    print("|---|---:|")
    print(f"| raw keys (config string + seed) | {fmt(raw)} |")
    print(f"| (config id, uint64 seed) pairs, 1+8 bytes | {fmt(9 * len(points))} |")
    print(f"| bitmap over 2^32 seeds x {len(set(c for c, _ in points))} configs | "
          f"{fmt(SPACE / 8 * len(set(c for c, _ in points)))} |")
    print(f"| XORSAT filter, 20 solutions | {fmt(zoo.nbytes)} "
          f"(measured FPR {fpr_zoo:.2e} on 2M random keys, build {t_zoo * 1000:.0f} ms) |")
    print()

    # Query throughput for "is (S2, seed) swept?" on random seeds.
    rng = random.Random(3301)
    seeds = [rng.getrandbits(32) for _ in range(args.probes)]
    starts = [a for a, _ in ranges[S2]]
    ends = [b for _, b in ranges[S2]]

    def by_intervals():
        c = 0
        for s in seeds:
            i = bisect.bisect_right(starts, s) - 1
            c += i >= 0 and s < ends[i]
        return c

    bm = bytearray(NBLOCKS)
    for i in blocks[S2]:
        bm[i] = 1

    def by_bitmap():
        return sum(bm[s >> BLOCK_BITS] for s in seeds)

    def by_filter_loop():
        return sum(block_key(S2, s >> BLOCK_BITS) in exact for s in seeds)

    pre = S2.encode() + b"\x00B"
    packer = struct.Struct("<I").pack

    def by_filter_batch():
        keys = b"".join(pre + packer(s >> BLOCK_BITS) for s in seeds)
        return exact.count_many(keys, key_bytes=len(pre) + 4)

    keys_packed = b"".join(pre + packer(s >> BLOCK_BITS) for s in seeds)

    def by_filter_batch_prepacked():
        return exact.count_many(keys_packed, key_bytes=len(pre) + 4)

    print(f"Query throughput: {args.probes:,} random S2 seeds, single thread")
    print("| method | M queries/s | answered swept |")
    print("|---|---:|---:|")
    for name, fn_ in [("bisect on interval list (exact, per seed)", by_intervals),
                      ("bytearray bitmap (per block)", by_bitmap),
                      ("XORSAT exact, `key in f` loop", by_filter_loop),
                      ("XORSAT exact, build keys + count_many", by_filter_batch),
                      ("XORSAT exact, count_many on pre-packed keys", by_filter_batch_prepacked)]:
        t = time.perf_counter()
        c = fn_()
        dt = time.perf_counter() - t
        print(f"| {name} | {args.probes / dt / 1e6:.2f} | {c:,} |")
    print()
    print("Interval and block answers differ only for seeds in partially swept edge blocks.")


if __name__ == "__main__":
    main()
