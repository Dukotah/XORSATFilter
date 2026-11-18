# LANE 4: XORSATFilter

Branches on `origin` (github.com/Dukotah/XORSATFilter):

- `fix/build-hygiene`: one commit (`2ea6cfb`) on `upstream/master`. This is the upstream PR candidate.
- `lane/python-bindings`: branched from `fix/build-hygiene`. Adds `1afe9c3` (pyxorsat) and this report. Fork only.

## 1. Upstream contribution rules

- The repo has no CONTRIBUTING.md, no PR template and no CI. The license is CC0 (US-Gov work). DISCLAIMER.md applies.
- History has 0 issues and 1 PR. That PR was ByteHamster's bitvector submodule bump (#1, 2022-08-03). The maintainer, weaversa, closed it and made the bump himself the same day.
- What we can infer: keep changes small and in the existing style (C99, 2-space indent, `//` comments). Don't bump submodules, because he does that himself. Expect him to rewrite rather than merge.

## 2. What changed and why

### `fix/build-hygiene` (6 files, +66/-12)
- **`.gitmodules` and README clone commands now use https instead of `git@github.com:`.** With SSH URLs, `git clone --recursive` fails without a GitHub SSH key, and that includes CI. Submodule SHAs are unchanged.
- **The test now fails when it should.** Before this change it always exited 0:
  - Its only correctness check, `assert(p == 1.0)`, was compiled out by `-DNDEBUG`.
  - Metadata mismatches were printed but not counted.
  - Metadata was compared with `strncmp`, which stops at the first 0 byte of the random binary metadata. It now uses `memcmp`.
  - The test returns 1 on any wrong answer or metadata mismatch. It keeps the assert and adds `assert(nMetaDataFailures == 0)`.
- **New `make check` target.** It builds `test/check` from the same `test.c` using `$(filter-out -DNDEBUG,$(CFLAGS))` and runs it. `clean` also removes `test/check`.
- **`include/xorsat_filter.h`:** `#define XORSATFILTER_PRINT_BUILD_PROGRESS` is now wrapped in `#ifndef XORSATFILTER_NO_PRINT_BUILD_PROGRESS`, so progress output can be silenced with `-D` instead of editing the header. The default behavior is unchanged.
- **README changes:**
  - Documents the `XORSATFilterDW*Parameters` sets added in 2022.
  - Documents the new define and `make check`.
  - Fixes the `XORSATFilterSerialize` return values. The README had them backwards: the code returns 0 on success and 1 on failure (`src/xorsat_serial.c:10-33`).
- **`.github/workflows/ci.yml`:** a gcc/clang matrix that runs `make CC=… && make CC=… check` with `submodules: recursive`.
- **Not changed:** `-march=native` in the Makefile, which makes the `.a` non-portable. `make OPT=...` already overrides it, and changing the default is the maintainer's decision. It is noted in the issue text below as a follow-up.

### `lane/python-bindings` (fork only)
- **`python/pyxorsat`: cffi bindings.**
  - Classes: `Builder` (`add`, `add_many`, `add_absence`, `add_absences_many`, `finalize`) and `Filter` (`in`, `query_many`, `count_many`, `get`, `measure_fpr`, `save`/`load`, `to_bytes`/`from_bytes`, sizes).
  - Accepts the 6 named parameter sets or a custom dict.
  - A small C shim does batch add/query and random-key FPR measurement in one C call each.
  - The library sources are compiled straight from `../src` and the submodules, through one-line `#include` wrappers, because setuptools rejects source paths outside the project directory. Each file stays its own translation unit.
  - Built without `-march=native`, so the wheel is portable.
  - Built without `-ffast-math`. Linking a shared object with it pulls in `crtfastmath.o`, which turns on flush-to-zero for the whole Python process.
- **Tests:** 18 pytest tests. **Wheel:** `uv build --wheel -p 3.12`.
- **`python/BENCHMARK.md`:** compares against rbloom (a Bloom filter) and pyfusefilter (Xor8/16, Fuse8/16) with real numbers.
- **`python/examples/liber_primus_swept_seeds.py`:** see section 3c.

## 3. Evidence (WSL Ubuntu, gcc 15.2, clang 21.1.8, Core Ultra 7 258V)

### 3a. `fix/build-hygiene`
```
$ git submodule sync && git submodule update --init --recursive   # works over https, no SSH key
$ make -j6 && make check            # gcc
gcc -std=gnu99 -march=native -O3 -ffast-math -fomit-frame-pointer -finline-functions -Iinclude -Llib test/test.c -o test/check -lxorsatfilter -lm -lpthread
test/check
Building took 4 wallclock seconds and 15 CPU seconds
Percent passed = 100.0000%
Testing query speed with util func: 10942942 queries per second
Testing false positive rate with util func: 0.79179995%
Testing filter efficiency with util func: 98.58% efficient
Filter uses 7.08 bits per element
EXIT=0

$ make CC=clang -j6 && make CC=clang check
build_exit=0 ... Percent passed = 100.0000% ... Filter uses 7.08 bits per element
check_exit=0

# Sabotage: flip the answer for element 5 (`^ (i == 5)`) and rebuild
upstream test.c, -DNDEBUG (as `make test/test` builds it):
  Percent passed = 99.9999%   upstream_sab_exit=0      <- bug: wrong answer, exit 0
patched test.c, no -DNDEBUG (as `make check` builds it):
  sab_assert: test/sab.c:154: int main(int, char **): Assertion `p == 1.0' failed.
  sab_assert_exit=134
patched test.c, -DNDEBUG:
  Percent passed = 99.9999%   sab_ndebug_exit=1

# Quiet build
$ make OPT="-O3 -DNDEBUG -DXORSATFILTER_NO_PRINT_BUILD_PROGRESS" && make OPT=... check | grep -c "Processing block"
0          (check_exit=0)
```
The CI workflow has not run yet. GitHub Actions on the fork run only for pushes to branches when Actions are enabled on the fork, so the owner may need to enable them. Locally, the same two commands pass with both compilers.

### 3b. `lane/python-bindings`
```
$ uv build --wheel -p 3.12
dist/pyxorsat-0.1.0-cp312-cp312-linux_x86_64.whl  (232,759 bytes)
$ uv pip install dist/*.whl && pytest -q
..................                                                       [100%]
18 passed in 5.31s
```
Benchmark: see `python/BENCHMARK.md`. In short, at FPR 2^-7 the filter uses 7.08 bits/key, compared with 9.04 for Fuse8 and 10.10 for Bloom. At 2^-16 it uses 16.18 bits/key, compared with 18.09 and 23.08. Builds are about 10-30x slower: 5.5 s on 6 threads. The Python-side `in` loop is about 1.1 M q/s, and batch `count_many` is 5-9 M q/s.

### 3c. Liber Primus demo (`python examples/liber_primus_swept_seeds.py`, 0:36 wall, 483 MB peak RSS)
```
Sources
  - S2: 6 engine worker bands from analysis/round42/S1-s2-recovery/state_recovered/STATUS.json, 0 done volunteer shards
  - S1: lanes_completed in STATUS.json -> [0, 4294967296)
  - ledger:R25-COMPUTE-TAIL-CHUNK1: 2 ranges from the ledger coverage dict
  - LEDGER.json: 350 rows, 310 with a coverage field, 1 machine-readable seed ranges
  - seed zoo: 323 seeds x 7 generators x 2 decoders = 4522 (config, seed) points

Index over 3 configs x 2^20 blocks (1,594,568 swept, 1,551,160 unswept)
| structure | size | exact? | wrong 'swept' answers on unswept blocks |
| sorted interval list (2 x uint64 per interval, per-seed resolution) | 144 B | yes | 0 |
| bitmap, 1 bit per block per config | 384.0 KiB | yes | 0 |
| sorted uint32 list of swept blocks | 6.1 MiB | yes | 0 |
| XORSAT filter of swept blocks (dw-paper, 2^-7) | 1.3 MiB | no | 12,049 of 1,551,160 (0.78 %) |
| XORSAT, swept + unswept-as-absences (1 solution) | 392.8 KiB | yes, in-universe | 0 |

Seed zoo: 4,522 (config, seed) points
| (config id, uint64 seed) pairs, 1+8 bytes | 39.7 KiB |
| XORSAT filter, 20 solutions | 11.4 KiB (measured FPR 5.00e-07 on 2M random keys, build 68 ms) |

Query throughput: 1,000,000 random S2 seeds, single thread
| bisect on interval list (exact, per seed) | 6.06 |
| bytearray bitmap (per block) | 8.70 |
| XORSAT exact, `key in f` loop | 0.78 |
| XORSAT exact, build keys + count_many | 1.21 |
| XORSAT exact, count_many on pre-packed keys | 10.94 |
```
The demo's conclusion, stated in its docstring and output, is that the swept work is mostly contiguous ranges. For that data an interval list (144 B, exact, per seed) is the right structure. The plain filter would wrongly report 0.78 % of unswept blocks as "swept", which would make a solver silently skip work. The absences trick makes the filter exact, but it only matches a bitmap. The filter earns its place only on the sparse seed-zoo set. The cicada repo was only read, never written.

## 4. Competing work check
- `gh pr list -R NationalSecurityAgency/XORSATFilter --state all` returns one PR, #1 "Update bitvector submodule" (ByteHamster, closed 2022-08-03).
- `gh issue list --state all` returns none.
- The last push was 2022-08-04.
- None of the 8 most recently pushed forks have any newer activity, apart from this fork.
- No existing work covers https submodules, `make check`, CI or Python bindings.

## 5. Draft upstream text (the owner decides whether to post; nothing has been opened)

**PR title:** Use https submodule URLs and add a CI-able `make check`

**PR body:**
```
Three small build/test fixes, no library code changes, no submodule bumps:

1. `.gitmodules` uses `git@github.com:` URLs, so `git clone --recursive`
   fails for anyone without a GitHub SSH key (and for CI). Switched to https;
   the pinned submodule commits are unchanged.

2. `test/test.c` always exits 0. Its only correctness check,
   `assert(p == 1.0)`, is compiled out by the `-DNDEBUG` in `OPT`, and metadata
   mismatches are printed but not counted (and compared with `strncmp`, which
   stops at the first zero byte of the binary metadata). With a deliberately
   wrong answer injected for one element, the current test prints
   `Percent passed = 99.9999%` and exits 0. Now the test returns 1 on any wrong
   answer or metadata mismatch (memcmp), and a new `make check` target builds the
   same test without -DNDEBUG so the asserts fire too.

3. `XORSATFILTER_PRINT_BUILD_PROGRESS` is wrapped in
   `#ifndef XORSATFILTER_NO_PRINT_BUILD_PROGRESS`, so progress output can be
   turned off with `-D` instead of editing the header. Default unchanged.

README: documents the DW parameter sets, the new define and `make check`, and
fixes the XORSATFilterSerialize return values (the code returns 0 on success,
1 on failure; the README had them reversed).

Adds a GitHub Actions workflow running `make && make check` with gcc and clang.

Tested on Ubuntu (gcc 15.2, clang 21.1): both build and `make check` pass
(100% passed, 7.08 bits/element with DWPaper).

Possible follow-up, not in this PR: `-march=native` in OPT makes
lib/libxorsatfilter.a non-portable; `make OPT=...` works around it today.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## 6. Land probability and next steps
- **`fix/build-hygiene`: Med.** The repo has been dormant since 2022. The maintainer is responsive elsewhere, but on this repo's only outside PR he redid the change himself the same day. He may do the same here: take the idea and commit it himself.
- **`lane/python-bindings`: fork-only.** It is not meant for upstream.
- **Owner next steps:**
  1. Optionally enable Actions on the fork to see `ci.yml` run.
  2. Review the diff with `git diff upstream/master fix/build-hygiene`.
  3. Open the PR from `Dukotah:fix/build-hygiene` with the text above.
- **Commit trailer note:** LANE-RULES asks for `Co-Authored-By: Claude Fable 5.1`. These commits were written by Claude Opus 5.5 and say so accurately. Amend them if the program needs the uniform trailer.
