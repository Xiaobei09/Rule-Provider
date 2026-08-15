"""render / config 模块单元测试。"""

import ipaddress
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from render import (
    build_formats,
    render_metadata,
    render_rule_set,
)
from config import load_config


def _cn_nets():
    return [
        ipaddress.ip_network("1.0.0.0/24"),
        ipaddress.ip_network("2001:db8::/32"),
    ]


def test_surge_format():
    fmt = build_formats()["Surge"]
    text = render_rule_set(fmt, "CN", _cn_nets(), "test", True)
    assert "IP-CIDR,1.0.0.0/24,no-resolve" in text
    assert "IP-CIDR6,2001:db8::/32,no-resolve" in text


def test_clash_format():
    fmt = build_formats()["Clash"]
    text = render_rule_set(fmt, "CN", _cn_nets(), "test", True)
    assert text.lstrip().startswith("#")
    assert "payload:" in text
    assert "  - IP-CIDR,1.0.0.0/24,no-resolve" in text
    assert "  - IP-CIDR6,2001:db8::/32,no-resolve" in text


def test_quantumultx_format():
    fmt = build_formats()["QuantumultX"]
    text = render_rule_set(fmt, "CN", _cn_nets(), "test", True)
    assert "ip-cidr, 1.0.0.0/24" in text
    assert "ip6-cidr, 2001:db8::/32" in text


def test_loon_format():
    fmt = build_formats()["Loon"]
    text = render_rule_set(fmt, "CN", _cn_nets(), "test", True)
    assert "IP-CIDR,1.0.0.0/24,no-resolve" in text
    assert "IP-CIDR6,2001:db8::/32,no-resolve" in text


def test_metadata():
    doc = json.loads(
        render_metadata({"CN": _cn_nets()}, "test", "2026-01-01 00:00:00 UTC")
    )
    assert doc["country_count"] == 1
    assert doc["countries"]["CN"]["name_zh"] == "中国"
    assert doc["countries"]["CN"]["ipv4_cidrs"] == 1


def test_config_loads_default():
    cfg = load_config()
    assert cfg["ruleset_dir"] == "ruleset"
    assert cfg["global_provider"] is True
    assert len(cfg["sources"]["rir"]["urls"]) == 5
    assert cfg["sources"]["rir"]["statuses"] == ["allocated", "assigned"]
    assert "Surge" in cfg["formats"]
