"""cffi out-of-line API-mode build script for ``pyxorsat._xorsat``.

The extension is compiled straight from the C sources of the parent repository
(``../src`` and the ``../lib`` submodules), so no prior ``make`` is needed. The
flags differ from the top-level Makefile on purpose:

* no ``-march=native``: a wheel has to run on machines other than the build box;
* no ``-ffast-math``: linking a shared object with it pulls in ``crtfastmath.o``,
  which turns on flush-to-zero for the whole Python process;
* ``-DXORSATFILTER_NO_PRINT_BUILD_PROGRESS``: no progress chatter on stderr.
"""

import os
from pathlib import Path

from cffi import FFI

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("XORSATFILTER_ROOT", HERE.parent.parent)).resolve()

LIB_SOURCES = [
    "src/list_types.c", "src/xorsat_hashes.c", "src/xorsat_metadata.c",
    "src/MurmurHash3.c", "src/xorsat_blocks.c", "src/xorsat_solve.c",
    "src/xorsat_immir_wrap.c", "src/xorsat_serial.c", "src/xorsat_build.c",
    "src/xorsat_query.c", "src/immir.c",
    "lib/bitvector/src/bitvector.c",
    "lib/C-Thread-Pool/thpool.c",
]

_missing = [s for s in LIB_SOURCES if not (ROOT / s).exists()]
if _missing:
    raise RuntimeError(
        f"XORSATFilter sources not found under {ROOT}: {_missing}. Run "
        "`git submodule update --init --recursive` in the repository root, "
        "or point XORSATFILTER_ROOT at a checkout."
    )

CDEF = """
typedef struct XORSATFilterParameters {
  uint8_t nLitsPerRow;
  uint8_t nSolutions;
  uint16_t nEltsPerBlock;
  double fEfficiency;
} XORSATFilterParameters;

extern XORSATFilterParameters XORSATFilterEfficientParameters;
extern XORSATFilterParameters XORSATFilterPaperParameters;
extern XORSATFilterParameters XORSATFilterFastParameters;
extern XORSATFilterParameters XORSATFilterDWEfficientParameters;
extern XORSATFilterParameters XORSATFilterDWPaperParameters;
extern XORSATFilterParameters XORSATFilterDWFastParameters;

typedef struct XORSATFilterBuilder XORSATFilterBuilder;
typedef struct XORSATFilterQuerier XORSATFilterQuerier;

XORSATFilterBuilder *XORSATFilterBuilderAlloc(uint32_t nExpectedElements, size_t nMetaDataBytes);
void XORSATFilterBuilderFree(XORSATFilterBuilder *xsfb);
uint8_t XORSATFilterBuilderAddElement(XORSATFilterBuilder *xsfb, const void *pElement, size_t nElementBytes, const void *pMetaData);
uint8_t XORSATFilterBuilderAddAbsence(XORSATFilterBuilder *xsfb, const void *pElement, size_t nElementBytes);
XORSATFilterQuerier *XORSATFilterBuilderFinalize(XORSATFilterBuilder *xsfb, XORSATFilterParameters sParams, uint32_t nThreads);
void XORSATFilterQuerierFree(XORSATFilterQuerier *xsfq);

uint8_t XORSATFilterQuery(XORSATFilterQuerier *xsfq, const void *pElement, uint32_t nElementBytes);
uint8_t *XORSATFilterRetrieveMetadata(XORSATFilterQuerier *xsfq, const void *pElement, uint32_t nElementBytes);

uint64_t XORSATAncillarySize(XORSATFilterQuerier *xsfq);
uint64_t XORSATFilterSize(XORSATFilterQuerier *xsfq);
uint64_t XORSATMetaDataSize(XORSATFilterQuerier *xsfq);

size_t pyxs_meta_bytes(XORSATFilterQuerier *xsfq);
uint8_t pyxs_add_many(XORSATFilterBuilder *xsfb, const uint8_t *pKeys, size_t nKeyBytes,
                      size_t nKeys, const uint8_t *pMeta, size_t nMetaBytes, int absent);
size_t pyxs_query_many(XORSATFilterQuerier *xsfq, const uint8_t *pKeys, size_t nKeyBytes,
                       size_t nKeys, uint8_t *pOut);
uint64_t pyxs_count_random_hits(XORSATFilterQuerier *xsfq, uint64_t nQueries, uint64_t seed);
int pyxs_serialize_path(XORSATFilterQuerier *xsfq, const char *path);
XORSATFilterQuerier *pyxs_deserialize_path(const char *path);
void free(void *);
"""

SHIM = r"""
#include "xorsat_filter.h"

size_t pyxs_meta_bytes(XORSATFilterQuerier *xsfq) { return xsfq->nMetaDataBytes; }

/* Add nKeys fixed-width keys packed back to back. Returns 0 on success. */
uint8_t pyxs_add_many(XORSATFilterBuilder *xsfb, const uint8_t *pKeys, size_t nKeyBytes,
                      size_t nKeys, const uint8_t *pMeta, size_t nMetaBytes, int absent) {
  size_t i;
  for(i = 0; i < nKeys; i++) {
    uint8_t ret;
    if(absent) {
      ret = XORSATFilterBuilderAddAbsence(xsfb, pKeys + i*nKeyBytes, nKeyBytes);
    } else {
      ret = XORSATFilterBuilderAddElement(xsfb, pKeys + i*nKeyBytes, nKeyBytes,
                                          pMeta == NULL ? NULL : pMeta + i*nMetaBytes);
    }
    if(ret != 0) return ret;
  }
  return 0;
}

/* Query nKeys fixed-width keys; pOut[i] = 0/1 (pOut may be NULL). Returns #positives. */
size_t pyxs_query_many(XORSATFilterQuerier *xsfq, const uint8_t *pKeys, size_t nKeyBytes,
                       size_t nKeys, uint8_t *pOut) {
  size_t i, nHits = 0;
  for(i = 0; i < nKeys; i++) {
    uint8_t r = XORSATFilterQuery(xsfq, pKeys + i*nKeyBytes, (uint32_t) nKeyBytes);
    if(pOut != NULL) pOut[i] = r;
    nHits += r;
  }
  return nHits;
}

static uint64_t pyxs_splitmix64(uint64_t *s) {
  uint64_t z = (*s += 0x9e3779b97f4a7c15ULL);
  z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
  z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
  return z ^ (z >> 31);
}

/* Query nQueries random 17-byte keys and count positives. Element bytes are
   hashed with their length, so as long as no real element is 17 bytes long,
   every positive here is a false positive. */
uint64_t pyxs_count_random_hits(XORSATFilterQuerier *xsfq, uint64_t nQueries, uint64_t seed) {
  uint64_t i, nHits = 0, s = seed;
  uint8_t key[17];
  key[16] = 0xA5;
  for(i = 0; i < nQueries; i++) {
    uint64_t a = pyxs_splitmix64(&s), b = pyxs_splitmix64(&s);
    memcpy(key, &a, 8);
    memcpy(key + 8, &b, 8);
    nHits += XORSATFilterQuery(xsfq, key, sizeof(key));
  }
  return nHits;
}

int pyxs_serialize_path(XORSATFilterQuerier *xsfq, const char *path) {
  FILE *f = fopen(path, "wb");
  if(f == NULL) return 1;
  int ret = XORSATFilterSerialize(f, xsfq);
  if(fclose(f) != 0) ret = 1;
  return ret;
}

XORSATFilterQuerier *pyxs_deserialize_path(const char *path) {
  FILE *f = fopen(path, "rb");
  if(f == NULL) return NULL;
  XORSATFilterQuerier *xsfq = XORSATFilterDeserialize(f);
  fclose(f); /* the mmap()ed filter stays valid after the descriptor is closed */
  return xsfq;
}
"""


def _wrapper_sources():
    """setuptools only accepts source paths inside the project (python/), so each
    library .c file gets a one-line wrapper under pyxorsat/_csrc that #includes it.
    Every file stays its own translation unit, exactly as in the Makefile."""
    out = HERE / "_csrc"
    out.mkdir(exist_ok=True)
    rel = []
    for s in LIB_SOURCES:
        w = out / s.replace("/", "__")
        text = f'#include "{(ROOT / s).as_posix()}"\n'
        if not w.exists() or w.read_text() != text:
            w.write_text(text)
        rel.append(f"pyxorsat/_csrc/{w.name}")
    return rel


ffibuilder = FFI()
ffibuilder.cdef(CDEF)
ffibuilder.set_source(
    "pyxorsat._xorsat",
    SHIM,
    sources=_wrapper_sources(),
    include_dirs=[str(ROOT / "include"), str(ROOT / "lib" / "bitvector" / "include")],
    define_macros=[("NDEBUG", None), ("XORSATFILTER_NO_PRINT_BUILD_PROGRESS", None)],
    extra_compile_args=["-std=gnu99", "-O3", "-fomit-frame-pointer", "-w"],
    libraries=["m", "pthread"],
)

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
