"""IPtoASN 数据源模块单元测试。"""

import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from iptoasn import (  # noqa: E402
    _parse_http_date,
    load_date,
    parse_tsv_gz,
    save_date,
    split_range_to_cidrs,
)


def _gzip(text: str) -> bytes:
    return gzip.compress(text.encode())


def test_split_range_single_ip():
    assert split_range_to_cidrs(1, 1, 32) == [(1, 32)]


def test_split_range_aligned_pair():
    # 1.0.1.0 - 1.0.3.255 -> /24 + /23
    nets = split_range_to_cidrs(16777472, 16778239, 32)
    assert nets == [(16777472, 24), (16777728, 23)]


def test_split_range_full_v4():
    assert split_range_to_cidrs(0, 2**32 - 1, 32) == [(0, 0)]


def test_split_range_v6_non_aligned():
    # 64:ff9b::1:0:0 - 64:ff9b::1:ffff:ffff（一个 /96 对齐块）
    base = (0x64 << 112) | (0xFF9B << 96)
    s = base | (1 << 32)
    e = s + (1 << 32) - 1
    blocks = split_range_to_cidrs(s, e, 128)
    assert blocks == [(s, 96)]
    total = sum(1 << (128 - ln) for _s, ln in blocks)
    assert total == 1 << 32


def test_split_range_coverage_exact():
    # 随机范围拆分后覆盖地址数必须等于区间长度
    start, end = 1000, 1000 + 4999
    blocks = split_range_to_cidrs(start, end, 32)
    total = sum(1 << (32 - ln) for _s, ln in blocks)
    assert total == 5000


def test_parse_tsv_skips_unrouted():
    text = (
        "1.0.0.0\t1.0.0.255\t13335\tUS\tCLOUDFLARENET\n"
        "0.0.0.0\t255.255.255.255\t0\tNone\tNot routed\n"
        "1.0.1.0\t1.0.3.255\t38803\tAU\tGtelecom\n"
    )
    out = parse_tsv_gz(_gzip(text), 4)
    assert "US" in out and "AU" in out
    assert sorted(str(n) for n in out["AU"]) == ["1.0.1.0/24", "1.0.2.0/23"]
    assert all("0.0.0.0" not in str(n) for nets in out.values() for n in nets)


def test_parse_tsv_invalid_lines():
    text = (
        "bad\tline\n"
        "1.1.1.1\t1.1.1.1\tAS15169\t999\tok\n"  # 非 2 字母国家码
        "2.2.2.2\t2.2.2.2\t0\tZZ\tNot routed\n"  # ASN=0
        "3.3.3.3\t3.3.3.300\t100\tZZ\tbad addr\n"  # 非法地址
    )
    out = parse_tsv_gz(_gzip(text), 4)
    assert "999" not in out
    assert "ZZ" not in out
    assert out == {}


def test_parse_tsv_ipv6():
    text = "::1\t::1\t0\tNone\tNot routed\n2001:db8::\t2001:db8::ffff\t1\tDE\tTest\n"
    out = parse_tsv_gz(_gzip(text), 6)
    assert sorted(str(n) for n in out["DE"]) == ["2001:db8::/112"]


def test_http_date_parsing():
    assert _parse_http_date("Sat, 15 Aug 2026 03:46:37 GMT") == "2026-08-15"
    assert _parse_http_date("") is None
    assert _parse_http_date("not-a-date") is None


def test_date_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        cache = Path(td)
        save_date(cache, "2026-08-15")
        assert load_date(cache) == "2026-08-15"
        save_date(cache, None)
        assert load_date(cache) is None
