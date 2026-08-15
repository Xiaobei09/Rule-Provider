"""数据源模块：从互联网搜集并解析 IP -> 国家分配数据。

数据源（详见 DEVELOPMENT.md「数据源」）：
1. 五大 RIR delegated 扩展文件（默认）：AFRINIC / APNIC / ARIN / LACNIC / RIPE
   各自发布的权威分配数据，需分别下载后合并以获得全球覆盖。解析规则：
   - 行格式：`registry|cc|type|start|value|date|status`；
   - type 仅保留 ipv4 / ipv6；
   - 仅保留 status 属于 config 指定集合（allocated / assigned）的段；
   - IPv4 value 为地址数量（可能非 2 的幂，需拆分为多个 CIDR）；
   - IPv6 value 直接为前缀长度。
2. MaxMind GeoLite2-Country-CSV（可选）：真实地理归属更精确，需要免费 License Key，
   通过环境变量 MAXMIND_LICENSE_KEY 提供；用于对 RIR 数据做交叉校验/补充。

返回结构：{CC: list[IPv4Network|IPv6Network]}，国家代码为大写。
"""

from __future__ import annotations

import ipaddress
import io
import math
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from cidr import Network, build_network, merge_networks

# type 列忽略非 IP 记录
_SKIP_TYPES = {"asn"}


class SourceError(RuntimeError):
    pass


@dataclass
class FetchResult:
    """一次抓取的解析结果。"""

    country_networks: dict[str, list[Network]]
    fetched_at: str
    records_total: int
    records_used: int


def _download(url: str, cache_path: Path, timeout: int, user_agent: str) -> Path:
    """下载文件到缓存。

    使用 HTTP Range 分块 + 单块重试，避免网络抖动/代理截断导致整文件重下；
    对 GitHub Actions 与大文件（RIR 约 10MB+）更稳定。
    """
    import urllib.request

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    chunk = 4 * 1024 * 1024
    headers = {"User-Agent": user_agent}

    def _open(**extra):
        h = {**headers, **extra}
        return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout)

    try:
        with _open(method="HEAD") as resp:  # type: ignore[call-arg]
            total = int(resp.headers["Content-Length"])
    except Exception:  # noqa: BLE001
        total = None

    tmp = cache_path.with_name(cache_path.name + ".part")
    try:
        if total is not None:
            with open(tmp, "wb") as fh:
                for start in range(0, total, chunk):
                    end = min(start + chunk - 1, total - 1)
                    fh.write(_fetch_range(_open, start, end))
        else:
            with _open() as resp:
                tmp.write_bytes(resp.read())
        tmp.replace(cache_path)
    finally:
        tmp.unlink(missing_ok=True)
    return cache_path


def _fetch_range(open_fn, start: int, end: int, depth: int = 0) -> bytes:
    """带自适应分块的区间下载：失败时对半拆分继续。"""
    if depth > 8:
        raise SourceError(f"下载失败: 区间 {start}-{end} 无法完成")
    last: Exception | None = None
    for _ in range(4):
        try:
            with open_fn(Range=f"bytes={start}-{end}") as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
    if start == end:
        raise SourceError(f"下载失败: 单字节 {start} 无法获取 -> {last}")
    mid = (start + end) // 2
    return _fetch_range(open_fn, start, mid, depth + 1) + _fetch_range(
        open_fn, mid + 1, end, depth + 1
    )


def _split_ipv4(addr: ipaddress.IPv4Address, count: int) -> list[ipaddress.IPv4Network]:
    """把 (起始地址, 地址数量) 拆成规范 CIDR 列表（count 允许非 2 的幂）。"""
    nets: list[ipaddress.IPv4Network] = []
    start = int(addr)
    remaining = count
    while remaining > 0:
        align = start & -start
        max_block = 1 << (align.bit_length() - 1) if align else 1 << 32
        while max_block > remaining:
            max_block >>= 1
        prefix = 32 - int(math.log2(max_block))
        nets.append(ipaddress.IPv4Network((start, prefix), strict=False))
        start += max_block
        remaining -= max_block
    return nets


def parse_delegated(
    text: str, statuses: Iterable[str]
) -> dict[str, list[Network]]:
    """解析任一 RIR 的 delegated 扩展格式文本。"""
    wanted = {s.lower() for s in statuses}
    out: dict[str, list[Network]] = {}
    total = 0
    used = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) < 7:
            continue
        registry, cc, kind, start, value, _date, status = fields[:7]
        total += 1
        cc = cc.strip().upper()
        if not cc or kind in _SKIP_TYPES or status.lower() not in wanted:
            continue
        try:
            nets: list[Network]
            if kind == "ipv4":
                nets = _split_ipv4(ipaddress.IPv4Address(start), int(value))
            elif kind == "ipv6":
                nets = [build_network(int(ipaddress.IPv6Address(start)), int(value), 6)]
            else:
                continue
        except (ValueError, ipaddress.AddressValueError):
            continue
        out.setdefault(cc, []).extend(nets)
        used += 1
    return out, total, used


def fetch_rir(urls: list[str], cache_dir: Path, timeout: int, user_agent: str) -> dict[str, Path]:
    """下载全部 RIR 文件到缓存，返回 {url: 本地路径}。"""
    paths: dict[str, Path] = {}
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        paths[url] = _download(url, cache_dir / name, timeout, user_agent)
    return paths


def parse_maxmind_zip(data: bytes) -> dict[str, list[Network]]:
    """解析 GeoLite2-Country-CSV 压缩包，返回 {CC: [networks]}。"""
    geoname_to_cc: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        loc = next((n for n in names if "Locations" in n and n.endswith(".csv")), None)
        if loc is None:
            raise SourceError("GeoLite2 zip 中未找到 Locations CSV")
        with zf.open(loc) as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8"):
                if line.startswith("geoname_id"):
                    continue
                parts = line.rstrip("\n").split(",")
                if len(parts) < 6:
                    continue
                gid, cc = parts[0], parts[4]
                if gid and cc and cc.isalpha() and len(cc) == 2:
                    geoname_to_cc[gid] = cc.upper()

        out: dict[str, list[Network]] = {}
        for blk in names:
            if not blk.endswith(".csv") or "Blocks" not in blk:
                continue
            with zf.open(blk) as fh:
                for line in io.TextIOWrapper(fh, encoding="utf-8"):
                    if line.startswith("network"):
                        continue
                    parts = line.rstrip("\n").split(",")
                    if len(parts) < 5:
                        continue
                    network = parts[0]
                    geoname_id = parts[1]
                    registered_geoname_id = parts[2]
                    cc = (
                        geoname_to_cc.get(registered_geoname_id)
                        or geoname_to_cc.get(geoname_id)
                    )
                    if not cc:
                        continue
                    try:
                        net = ipaddress.ip_network(network, strict=False)
                    except ValueError:
                        continue
                    out.setdefault(cc, []).append(net)
    return out


def delegated_date(text: str) -> str | None:
    """从 delegated 文件头部版本行提取数据日期（YYYY-MM-DD）。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("2|"):
            continue
        parts = line.split("|")
        if len(parts) > 2 and parts[2].isdigit() and len(parts[2]) == 8:
            d = parts[2]
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return None
    return None


def fetch_maxmind(cache_path: Path, license_key: str, timeout: int, user_agent: str) -> Path:
    url = (
        "https://download.maxmind.com/app/geoip_download?"
        "edition_id=GeoLite2-Country-CSV&license_key={license}&suffix=zip"
    ).format(license=license_key)
    return _download(url, cache_path, timeout, user_agent)


def fetch_geosite(cache_path: Path, url: str, timeout: int, user_agent: str) -> Path:
    """下载 v2fly/domain-list-community 数据压缩包到缓存。"""
    return _download(url, cache_path, timeout, user_agent)


def load_from_cache(cache_path: Path) -> bytes:
    if not cache_path.exists():
        raise SourceError(f"未找到缓存数据 {cache_path}，请先执行 fetch")
    return cache_path.read_bytes()


def resolve_countries(
    sources: dict[str, list[Network]],
    merge: Callable[[list[Network]], list[Network]] = merge_networks,
) -> dict[str, list[Network]]:
    """合并多个来源（GEO 优先）为每国最终网络集合。"""
    result: dict[str, list[Network]] = {}
    for cc, nets in sources.items():
        result[cc] = merge(nets)
    return result


def env_license_key() -> str | None:
    return os.environ.get("MAXMIND_LICENSE_KEY") or None
