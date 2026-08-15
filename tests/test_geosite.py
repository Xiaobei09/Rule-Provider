"""geosite（域名）模块单元测试。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from geosite import (
    FALLBACK_CCTLD,
    build_cc_tld_map,
    parse_tld_list,
    resolve_category,
)
from render import (
    build_formats,
    render_site_metadata,
    render_site_rule_set,
)


def _sample_data():
    return {
        "cn": "include:tld-cn\ninclude:geolocation-cn\n",
        "tld-cn": "cn\nbaidu\nalibaba\n",
        "geolocation-cn": (
            "# 注释\n"
            "example.com\n"
            "domain:test.cn\n"
            "full:exact.example.org\n"
            "keyword:qq\n"
            "regexp:^.+\\\\.xunlei\\\\.com$\n"
            "attribute:ads:all\n"
            "domain:foo.com @ads\n"
            "include:extra-cn\n"
        ),
        "extra-cn": "extra.cn\n",
        "tld-ru": "ru\nsu\nmoscow\n",
        "tld-!cn": (
            "de # Germany\n"
            "jp # Japan\n"
            "us # United States of America (USA)\n"
            "gb # The United Kingdom of Great Britain and Northern Ireland\n"
            "uk # United Kingdom (UK)\n"
            "kr # Republic of Korea\n"
            "tw # Taiwan (Republic of China)\n"
            "mo # Macau\n"
            "eu # European Union\n"
            "cd # Congo, Democratic Republic of the (Congo-Kinshasa)\n"
            "ci # Côte d’Ivoire (Ivory Coast)\n"
            "richardli # Pacific Century Asset Management (HK) Limited\n"
            "gov\n"
            "mil\n"
            "include:tld-ru\n"
        ),
    }


def test_resolve_category_rules():
    rules = resolve_category(_sample_data(), "cn")
    kinds = {r.kind for r in rules}
    assert "domain" in kinds and "full" in kinds and "keyword" in kinds and "regexp" in kinds
    assert ("domain", "cn") in {(r.kind, r.value) for r in rules}
    assert ("domain", "extra.cn") in {(r.kind, r.value) for r in rules}
    assert ("domain", "foo.com") in {(r.kind, r.value) for r in rules}
    # 属性行被剥离，attribute: 被跳过
    assert not any(r.value.startswith("ads:") for r in rules)


def test_resolve_category_circular_safe():
    data = {"a": "include:b\n", "b": "include:a\ndomain:b.example\n"}
    rules = resolve_category(data, "a")
    assert ("domain", "b.example") in {(r.kind, r.value) for r in rules}


def test_build_cc_tld_map():
    mp = build_cc_tld_map(_sample_data()["tld-!cn"])
    assert mp["DE"] == ["de"]
    assert mp["US"] == ["us"]
    assert mp["GB"] == ["uk"]  # 优先采用 uk，而非 gb（后者映射到别名）
    assert mp["KR"] == ["kr"]
    assert mp["TW"] == ["tw"]
    assert mp["MO"] == ["mo"]
    assert mp["EU"] == ["eu"]
    assert mp["CD"] == ["cd"]
    assert mp["CI"] == ["ci"]
    # 公司 TLD（带 (HK) 的公司名）不归属 HK
    assert "HK" not in mp or "richardli" not in mp.get("HK", [])
    # 通用 TLD 不归属国家
    assert "gov" not in [t for v in mp.values() for t in v]


def test_fallback_cctld_has_curated_entries():
    assert FALLBACK_CCTLD["HK"] == ["hk"]
    assert FALLBACK_CCTLD["KP"] == ["kp"]
    assert FALLBACK_CCTLD["CO"] == ["co"]


def test_parse_tld_list():
    assert parse_tld_list(_sample_data()["tld-ru"]) == ["ru", "su", "moscow"]


def test_site_render_formats():
    rules = [("domain", "example.de"), ("full", "exact.example.de"), ("keyword", "de"), ("regexp", r"^.+\.de$")]
    for name in ("Surge", "Clash", "QuantumultX", "Loon"):
        fmt = build_formats()[name]
        text = render_site_rule_set(fmt, "DE", rules, "test", "2026-01-01 00:00:00 UTC")
        assert "# Rule-Provider: DE" in text
        assert "域名" in text
    s = render_site_rule_set(build_formats()["Surge"], "DE", [("domain", "example.de")], "test")
    assert "DOMAIN-SUFFIX,example.de" in s
    c = render_site_rule_set(build_formats()["Clash"], "DE", [("domain", "example.de")], "test")
    assert "payload:" in c and "  - DOMAIN-SUFFIX,example.de" in c
    q = render_site_rule_set(build_formats()["QuantumultX"], "DE", [("domain", "example.de")], "test")
    assert "host-suffix, example.de" in q
    l = render_site_rule_set(build_formats()["Loon"], "DE", [("domain", "example.de")], "test")
    assert "DOMAIN-SUFFIX,example.de" in l


def test_site_metadata():
    doc = __import__("json").loads(
        render_site_metadata({"DE": [("domain", "example.de")]}, "test", "2026-01-01 00:00:00 UTC")
    )
    assert doc["type"] == "site"
    assert doc["country_count"] == 1
    assert doc["countries"]["DE"]["rules"]["domain"] == 1
