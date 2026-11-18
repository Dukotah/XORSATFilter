import os
import struct

import pytest

from pyxorsat import PARAMETERS, Builder, Filter, XORSATError


def keys(n, tag=b"k"):
    return [tag + struct.pack("<Q", i) for i in range(n)]


@pytest.fixture(scope="module")
def filt():
    b = Builder(expected=50_000)
    b.add_many(keys(50_000))
    f = b.finalize("dw-fast")
    b.close()
    yield f
    f.close()


def test_no_false_negatives(filt):
    assert filt.count_many(keys(50_000)) == 50_000
    assert all(k in filt for k in keys(1000))


def test_fpr_is_near_two_to_minus_seven(filt):
    # all shipped parameter sets use nSolutions = 7 -> about 1/128
    fpr = filt.measure_fpr(400_000)
    assert 0.004 < fpr < 0.012


def test_query_many_matches_scalar(filt):
    probe = keys(2000) + keys(2000, tag=b"x")
    got = filt.query_many(probe)
    assert list(got) == [int(k in filt) for k in probe]
    assert got[:2000] == b"\x01" * 2000


def test_packed_buffer(filt):
    packed = b"".join(keys(100))
    assert filt.count_many(packed, key_bytes=9) == 100
    with pytest.raises(ValueError):
        filt.count_many(packed + b"z", key_bytes=9)


def test_size_is_near_optimal(filt):
    # 7 solutions -> ~7 bits/element at 100 % efficiency
    assert 7.0 <= filt.bits_per_element < 7.6
    assert filt.nbytes * 8 >= filt.filter_bits


def test_absences_are_honoured():
    b = Builder()
    b.add_many(keys(20_000))
    neg = keys(20_000, tag=b"n")
    b.add_absences_many(neg)
    f = b.finalize("dw-fast")
    assert f.count_many(keys(20_000)) == 20_000
    assert f.count_many(neg) == 0


def test_dictionary_roundtrip(tmp_path):
    b = Builder(meta_bytes=4)
    ks = keys(10_000)
    for i, k in enumerate(ks):
        b.add(k, struct.pack("<I", i * 7))
    f = b.finalize("dw-paper")
    assert f.meta_bytes == 4
    assert all(struct.unpack("<I", f.get(k))[0] == i * 7 for i, k in enumerate(ks))

    p = tmp_path / "d.xor"
    f.save(p)
    assert os.path.getsize(p) == f.nbytes
    g = Filter.load(p)
    assert g.meta_bytes == 4
    assert struct.unpack("<I", g.get(ks[1234]))[0] == 1234 * 7


def test_add_many_with_metas():
    b = Builder(meta_bytes=2)
    ks = keys(5000)
    b.add_many(ks, metas=b"".join(struct.pack("<H", i) for i in range(5000)))
    f = b.finalize()
    assert struct.unpack("<H", f.get(ks[4321]))[0] == 4321


def test_bytes_roundtrip(filt):
    data = filt.to_bytes()
    assert len(data) == filt.nbytes
    g = Filter.from_bytes(data)
    assert g.count_many(keys(50_000)) == 50_000
    probe = keys(5000, tag=b"y")
    assert g.query_many(probe) == filt.query_many(probe)


def test_str_keys():
    b = Builder()
    for w in ["alpha", "beta", "gamma"]:
        b.add(w)
    f = b.finalize("fast")
    assert "beta" in f and b"beta" in f


@pytest.mark.parametrize("name", sorted(PARAMETERS))
def test_every_parameter_set(name):
    b = Builder()
    b.add_many(keys(5000))
    f = b.finalize(name)
    assert f.count_many(keys(5000)) == 5000


def test_custom_parameters():
    b = Builder()
    b.add_many(keys(5000))
    f = b.finalize({"lits_per_row": 2, "solutions": 12, "elts_per_block": 1024})
    assert f.count_many(keys(5000)) == 5000
    assert f.measure_fpr(400_000) < 0.001


def test_errors():
    with pytest.raises(XORSATError):
        Builder().finalize()
    with pytest.raises(ValueError):
        Builder().add(b"a", b"meta")
    with pytest.raises(ValueError):
        Builder(meta_bytes=4).add(b"a", b"xx")
    with pytest.raises(ValueError):
        b = Builder()
        b.add(b"a")
        b.finalize("nope")
    with pytest.raises(TypeError):
        Builder().add(123)
    with pytest.raises(XORSATError):
        Filter.load("/nonexistent/filter.xor")
