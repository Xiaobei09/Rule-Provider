"""IPtoASN 数据源：免费 IP -> ASN/国家映射（TSV，Public Domain）。

数据源：https://iptoasn.com/（PDDL v1.0，每小时更新，无需 token）。
- 每国行格式（制表符分隔，无表头）：
      range_start  range_end  AS_number  country_code  AS_description
- `AS_number == 0`（未路由）或 `country_code == None` 的行一律跳过；
- 每行闭区间 [start, end] 拆分为最少个规范 CIDR（允许非 2 的幂）；
- 文件无内嵌版本日期：数据日期取 HTTP `Last-Modified` 头并保存到缓存，
  保证同日重复生成输出一致（幂等，参见 DEVELOPMENT.md §2.6）。
"""

from __future__ import annotations

import email.utils
import gzip
import io
import ipaddress
import urllib.request
from pathlib import Path

from cidr import Network, build_network
from sources import _download

IP2ASN_V4_URL = "https://iptoasn.com/data/ip2asn-v4.tsv.gz"
IP2ASN_V6_URL = "https://iptoasn.com/data/ip2asn-v6.tsv.gz"
_CACHE_V4 = "ip2asn-v4.tsv.gz"
_CACHE_V6 = "ip2asn-v6.tsv.gz"
_DATE_FILE = "ip2asn-date.txt"


def split_range_to_cidrs(start: int, end: int, bits: int) -> list[tuple[int, int]]:
    """把闭区间 [start, end] 拆成最少个 (首地址整数, 前缀长度) 规范 CIDR。

    - 优先取对齐到 start 的最大块，再按剩余地址数收敛；
    - 与 sources._split_ipv4 的数学一致，但对 IPv6 同样适用。
    """
    nets: list[tuple[int, int]] = []
    cur = start
    while cur <= end:
        align = cur & -cur
        block = align if align else (1 << bits)
        remaining = end - cur + 1
        while block > remaining:
            block >>= 1
        length = bits - (block.bit_length() - 1)
        nets.append((cur, length))
        cur += block
    return nets


def _parse_line(fields: list[str], version: int) -> tuple[str, list[tuple[int, int]]] | None:
    """解析一行 TSV；未路由/非法行返回 None。"""
    if len(fields) < 5:
        return None
    start_raw, end_raw, asn, cc, _desc = fields[:5]
    cc = cc.strip().upper()
    if not (len(cc) == 2 and cc.isalpha()) or asn == "0":
        return None
    try:
        if version == 4:
            start = int(ipaddress.IPv4Address(start_raw))
            end = int(ipaddress.IPv4Address(end_raw))
        else:
            start = int(ipaddress.IPv6Address(start_raw))
            end = int(ipaddress.IPv6Address(end_raw))
    except (ValueError, ipaddress.AddressValueError):
        return None
    return cc, split_range_to_cidrs(start, end, 32 if version == 4 else 128)


def parse_tsv_gz(data: bytes, version: int) -> dict[str, list[Network]]:
    """解析 IPtoASN gzip TSV，返回 {CC: [networks]}。"""
    out: dict[str, list[Network]] = {}
    bits = 32 if version == 4 else 128
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
            parsed = _parse_line(line.split("\t"), version)
            if parsed is None:
                continue
            cc, blocks = parsed
            out.setdefault(cc, []).extend(
                build_network(first, length, version) for first, length in blocks
            )
    return out


def _parse_http_date(value: str) -> str | None:
    """把 HTTP Last-Modified 头解析为 YYYY-MM-DD；无法解析返回 None。"""
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return dt.strftime("%Y-%m-%d")


def last_modified_date(url: str, timeout: int, user_agent: str) -> str | None:
    """HEAD 请求获取数据日期（IPtoASN 文件无内嵌版本日期）。"""
    try:
        req = urllib.request.Request(
            url, method="HEAD", headers={"User-Agent": user_agent}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _parse_http_date(resp.headers.get("Last-Modified") or "")
    except Exception:  # noqa: BLE001
        return None


def _date_path(cache_dir: Path) -> Path:
    return cache_dir / _DATE_FILE


def save_date(cache_dir: Path, date_str: str | None) -> None:
    _date_path(cache_dir).write_text(date_str or "", encoding="utf-8")


def load_date(cache_dir: Path) -> str | None:
    path = _date_path(cache_dir)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def fetch_ip2asn(
    cfg: dict, cache_dir: Path, timeout: int, user_agent: str
) -> str | None:
    """下载 v4/v6 数据库到缓存并保存数据日期；返回数据日期。"""
    _download(
        cfg.get("url_v4", IP2ASN_V4_URL),
        cache_dir / cfg.get("archive_v4", _CACHE_V4),
        timeout,
        user_agent,
    )
    _download(
        cfg.get("url_v6", IP2ASN_V6_URL),
        cache_dir / cfg.get("archive_v6", _CACHE_V6),
        timeout,
        user_agent,
    )
    date = last_modified_date(cfg.get("url_v4", IP2ASN_V4_URL), timeout, user_agent)
    save_date(cache_dir, date)
    return date


def load_ip2asn(cache_dir: Path, cfg: dict) -> dict[str, list[Network]]:
    """从缓存解析每国网络集合（v4 + v6 合并）；缺文件返回空字典。"""
    out: dict[str, list[Network]] = {}
    for name, version in (
        (cfg.get("archive_v4", _CACHE_V4), 4),
        (cfg.get("archive_v6", _CACHE_V6), 6),
    ):
        path = cache_dir / name
        if not path.exists():
            continue
        for cc, nets in parse_tsv_gz(path.read_bytes(), version).items():
            out.setdefault(cc, []).extend(nets)
    return out
