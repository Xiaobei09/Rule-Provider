"""top sites（CrUX 各国热门网站）模块单元测试。"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from topsites import (  # noqa: E402
    PublicSuffixList,
    _decompress_partial,
    extract_domains,
    latest_months,
    parse_psl,
)


def _mini_psl() -> PublicSuffixList:
    return parse_psl(
        "// ===BEGIN ICANN DOMAINS===\n"
        "com\n"
        "cn\n"
        "uk\n"
        "ru\n"
        "co.uk\n"
        "ck\n"
        "*.ck\n"
        "!www.ck\n"
        "// ===BEGIN PRIVATE DOMAINS===\n"
        "blogspot.com\n"
    )


def test_parse_psl_rules():
    psl = _mini_psl()
    assert "com" in psl.rules
    assert "*.ck" in psl.rules
    assert "blogspot.com" in psl.rules
    assert "www.ck" in psl.exceptions
    assert "ck" not in psl.exceptions


def test_registrable_domain():
    psl = _mini_psl()
    cases = {
        "www.example.com": "example.com",
        "example.com": "example.com",
        "WWW.EXAMPLE.COM": "example.com",
        "foo.co.uk": "foo.co.uk",
        "a.b.co.uk": "b.co.uk",
        "www.ck": "www.ck",  # PSL 例外规则
        "foo.www.ck": "www.ck",
        "foo.bar.ck": "foo.bar.ck",  # 通配 *.ck -> bar.ck 为公开后缀
        "a.blogspot.com": "a.blogspot.com",  # PRIVATE 段后缀
        "sub.example.com.": "example.com",  # 尾点
    }
    for host, expected in cases.items():
        assert psl.registrable_domain(host) == expected, host


def test_registrable_domain_invalid():
    psl = _mini_psl()
    for host in ("1.2.3.4", "com", "cn", "localhost", "", "example.com:8080"):
        assert psl.registrable_domain(host) is None, host


def test_latest_months():
    tree = json.dumps(
        {
            "tree": [
                {"path": "data/country/cn/202507.csv.gz"},
                {"path": "data/country/cn/202607.csv.gz"},
                {"path": "data/country/de/202606.csv.gz"},
                {"path": "data/country/de/202607.csv.gz"},
                {"path": "data/country/us/202607.csv.gz"},
                {"path": "README.md"},
            ]
        }
    )
    assert latest_months(tree) == {"cn": "202607", "de": "202607", "us": "202607"}


def _csv(rows):
    return "origin,rank\n" + "\n".join(f"https://{h},{r}" for h, r in rows) + "\n"


def test_extract_domains_ordering_and_cap():
    psl = _mini_psl()
    rows = [
        ("www.aaa.com", "1000"),
        ("aaa.com", "1000"),
        ("sub.bbb.co.uk", "1000"),
        ("www.ccc.com", "10000"),
        ("www.ddd.com", "10000"),
        ("www.eee.com", "100000"),  # 桶外数据忽略
        ("1.2.3.4", "10000"),  # IP 忽略
    ]
    out = extract_domains(_csv(rows), psl, 3)
    # rank=1000 优先、去重、按文件顺序取满 top_n
    assert out == ["aaa.com", "bbb.co.uk", "ccc.com"]


def test_extract_domains_cc_tld_covered_kept():
    """非目标国 ccTLD 的域名保留（归属过滤在生成阶段按国处理）。"""
    psl = _mini_psl()
    out = extract_domains(_csv([("www.ozon.ru", "1000")]), psl, 100)
    assert out == ["ozon.ru"]


def test_decompress_partial_truncated():
    import io

    rows = "".join(f"https://site{i}.example.com,10000\n" for i in range(20000))
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as fh:
        fh.write(rows.encode())
    full = buf.getvalue()
    head = full[: len(full) * 2 // 3]  # 截断的 gzip 流
    text = _decompress_partial(head)
    assert text.startswith("https://site0.example.com")
    assert len(text) < len(rows)


def test_extract_domains_full_csv_text():
    psl = _mini_psl()
    text = "origin,rank\nhttps://a.example.com,1000\n"
    assert extract_domains(text, psl, 10) == ["example.com"]
