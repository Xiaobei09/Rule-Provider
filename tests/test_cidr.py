"""cidr 模块单元测试。"""

import ipaddress
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cidr import (
    build_network,
    filter_reserved,
    merge_networks,
    format_cidr,
    count_ips,
)


def test_split_ipv4_network_building():
    net = build_network(int(ipaddress.IPv4Address("1.0.0.0")), 24, 4)
    assert str(net) == "1.0.0.0/24"


def test_build_ipv6_network():
    net = build_network(int(ipaddress.IPv6Address("2001:db8::")), 32, 6)
    assert str(net) == "2001:db8::/32"


def test_merge_adjacent():
    nets = [
        ipaddress.ip_network("10.0.0.0/25"),
        ipaddress.ip_network("10.0.0.128/25"),
    ]
    merged = merge_networks(nets)
    assert merged == [ipaddress.ip_network("10.0.0.0/24")]


def test_merge_contained():
    nets = [
        ipaddress.ip_network("10.0.0.0/24"),
        ipaddress.ip_network("10.0.0.0/25"),
    ]
    merged = merge_networks(nets)
    assert merged == [ipaddress.ip_network("10.0.0.0/24")]


def test_merge_not_adjacent():
    nets = [
        ipaddress.ip_network("10.0.0.0/24"),
        ipaddress.ip_network("10.0.2.0/24"),
    ]
    merged = merge_networks(nets)
    assert len(merged) == 2


def test_merge_empty():
    assert merge_networks([]) == []


def test_filter_reserved_removes_private():
    nets = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("8.8.8.0/24"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("1.0.0.0/24"),
    ]
    kept = filter_reserved(nets)
    kept_str = {str(n) for n in kept}
    assert kept_str == {"8.8.8.0/24", "1.0.0.0/24"}


def test_filter_reserved_ipv6():
    nets = [
        ipaddress.ip_network("2001:db8::/32"),
        ipaddress.ip_network("fc00::/7"),
        ipaddress.ip_network("2001:4860::/32"),
    ]
    kept = filter_reserved(nets)
    kept_str = {str(n) for n in kept}
    assert kept_str == {"2001:4860::/32"}


def test_format_cidr_compresses_ipv6():
    net = ipaddress.ip_network("2001:0db8:0000:0000:0000:0000:0000:0000/32")
    assert format_cidr(net) == "2001:db8::/32"


def test_count_ips():
    nets = [
        ipaddress.ip_network("10.0.0.0/24"),
        ipaddress.ip_network("10.0.1.0/24"),
    ]
    assert count_ips(nets) == 512
