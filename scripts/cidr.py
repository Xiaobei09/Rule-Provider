"""CIDR 工具：合并、过滤、格式化。

所有生成规则的核心约束（详见 DEVELOPMENT.md）：
1. 输出必须是规范的 CIDR 前缀表示；
2. 合并仅允许相邻/包含关系，不得改变地址归属；
3. 特殊用途保留地址段一律剔除。
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, Sequence

IPv4 = ipaddress.IPv4Network
IPv6 = ipaddress.IPv6Network
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def as_network(value: str | int | Network) -> Network:
    """将字符串 / 整数 / Network 归一化为 IPv4Network / IPv6Network。"""
    if isinstance(value, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
        return value
    if isinstance(value, int):
        raise ValueError("整数必须配合版本使用，请改用 build_network()")
    return ipaddress.ip_network(str(value), strict=False)


def build_network(first: int, length: int, version: int) -> Network:
    """按首地址与掩码长度构造网络（用于 APNIC 行解析）。"""
    if version == 4:
        return ipaddress.IPv4Network((first, length), strict=False)
    return ipaddress.IPv6Network((first, length), strict=False)


def merge_networks(nets: Iterable[Network]) -> list[Network]:
    """将一组网络合并为互不包含、相邻合并后的最小集合。

    - 输入网络按 (版本, 首地址) 排序；
    - 仅当 b 完全被 a 包含或 b 与 a 相邻时合并；
    - 输出保证互不重叠、无包含关系、尽可能少的 CIDR。
    """
    result: dict[int, list[Network]] = {4: [], 6: []}
    for version in (4, 6):
        members = sorted(
            (n for n in nets if n.version == version),
            key=lambda n: (int(n.network_address), n.prefixlen),
        )
        result[version] = _merge_same_version(members)
    return result[4] + result[6]


def _merge_same_version(nets: Sequence[Network]) -> list[Network]:
    if not nets:
        return []
    merged: list[Network] = [nets[0]]
    for nxt in nets[1:]:
        prev = merged[-1]
        if nxt.subnet_of(prev):
            continue
        if _is_adjacent(prev, nxt) and _can_merge(prev, nxt):
            merged[-1] = _supernet(prev, nxt)
        else:
            merged.append(nxt)
    return merged


def _is_adjacent(a: Network, b: Network) -> bool:
    """a 与 b 相邻（a 的下一个地址恰好是 b 的首地址）。"""
    try:
        return int(a.broadcast_address) + 1 == int(b.network_address)
    except (ValueError, OverflowError):
        return False


def _can_merge(a: Network, b: Network) -> bool:
    """a 与 b 相邻且前缀长度相同，可合并成更大一档的前缀。"""
    if a.prefixlen != b.prefixlen:
        return False
    try:
        supernet = a.supernet()
    except ValueError:
        return False
    return supernet.prefixlen == a.prefixlen - 1 and b.subnet_of(supernet)


def _supernet(a: Network, b: Network) -> Network:
    return a.supernet()


def format_cidr(net: Network) -> str:
    """规范输出：IPv4 无压缩，IPv6 按 RFC 5952 压缩。"""
    return str(net)


def count_ips(nets: Iterable[Network]) -> int:
    """统计网络覆盖的 IP 总数（IPv4 按 1 计，IPv6 仅用于相对比较）。"""
    total = 0
    for n in nets:
        if n.version == 4:
            total += n.num_addresses
    return total


def ipv6_count(nets: Iterable[Network]) -> int:
    total = 0
    for n in nets:
        if n.version == 6:
            total += n.num_addresses
    return total


# ---------------------------------------------------------------------------
# IANA 特殊用途地址段（IPv4/IPv6 Special-Purpose Address Registries）
# 依据：https://www.iana.org/assignments/iana-ipv4-special-registry/
#       https://www.iana.org/assignments/iana-ipv6-special-registry/
# 这些段不应出现在国家规则中（回环/私网/链路本地/多播/保留等）。
# ---------------------------------------------------------------------------
RESERVED_IPV4: tuple[str, ...] = (
    "0.0.0.0/8",        # 本网络
    "10.0.0.0/8",       # 私网
    "100.64.0.0/10",    # 运营商级 NAT
    "127.0.0.0/8",      # 回环
    "169.254.0.0/16",   # 链路本地
    "172.16.0.0/12",    # 私网
    "192.0.0.0/24",     # IETF 协议分配
    "192.0.2.0/24",     # TEST-NET-1
    "192.88.99.0/24",   # 6to4 中继任播（已被 IANA 收回，仍按保留处理）
    "192.168.0.0/16",   # 私网
    "198.18.0.0/15",    # 基准测试
    "198.51.100.0/24",  # TEST-NET-2
    "203.0.113.0/24",   # TEST-NET-3
    "224.0.0.0/4",      # 多播
    "240.0.0.0/4",      # 保留
    "255.255.255.255/32",
)

RESERVED_IPV6: tuple[str, ...] = (
    "::/128",           # 未指定
    "::1/128",          # 回环
    "::ffff:0:0/96",    # IPv4 映射
    "64:ff9b::/96",     # NAT64
    "64:ff9b:1::/48",   # 本地使用 NAT64
    "100::/64",         # 丢弃前缀
    "2001::/23",        # IETF 协议分配
    "2001:2::/48",      # 基准测试
    "2001:db8::/32",    # 文档示例
    "2002::/16",        # 6to4
    "3fff::/20",        # 文档
    "5f00::/16",        # 分段路由
    "fc00::/7",         # 唯一本地地址 ULA
    "fe80::/10",        # 链路本地
    "ff00::/8",         # 多播
)

_RESERVED_CACHE: list[Network] | None = None


def reserved_networks() -> list[Network]:
    """构造保留段列表（惰性缓存）。"""
    global _RESERVED_CACHE
    if _RESERVED_CACHE is None:
        _RESERVED_CACHE = [
            ipaddress.ip_network(c, strict=False)
            for c in RESERVED_IPV4 + RESERVED_IPV6
        ]
    return _RESERVED_CACHE


def filter_reserved(nets: Iterable[Network]) -> list[Network]:
    """剔除 IANA 特殊用途保留段；不改变其余地址。"""
    reserved = reserved_networks()
    out: list[Network] = []
    for n in nets:
        removed = False
        for r in reserved:
            if r.version != n.version:
                continue
            if n.subnet_of(r):
                removed = True
                break
        if not removed:
            out.append(n)
    return out
