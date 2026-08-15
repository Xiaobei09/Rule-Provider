"""渲染模块：将每国网络集合渲染为各代理工具规则集文件。

支持格式（对齐 blackmatrix7 的分目录输出风格）：
- Surge       .list   `IP-CIDR,x.x.x.x/xx,no-resolve`
- Clash       .yaml   `payload:` 下 `- IP-CIDR,x.x.x.x/xx,no-resolve`
- QuantumultX .txt    `ip-cidr, x.x.x.x/xx` / `ip6-cidr, ...`
- Loon        .list   `IP-CIDR,x.x.x.x/xx,no-resolve`

格式细节约束见 DEVELOPMENT.md「输出格式规范」：
- 行尾无多余空白，文件以换行结尾；
- IPv6 一律使用 IP-CIDR6 / ip6-cidr 前缀；
- 注释以 `#` 开头，包含生成时间、来源、统计信息；
- 输出内容必须确定性排序，保证重复生成的 diff 为空。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cidr import Network
from countries import name_en, name_zh


@dataclass
class Format:
    """一种输出格式的渲染规则。"""

    name: str
    ext: str
    dir_name: str
    no_resolve: bool = False

    def line(self, net: Network, no_resolve: bool) -> str:  # noqa: ARG002
        raise NotImplementedError

    def payload(self, nets: list[Network], no_resolve: bool) -> list[str]:
        raise NotImplementedError


@dataclass
class SurgeFormat(Format):
    no_resolve: bool = False

    def line(self, net: Network, no_resolve: bool) -> str:
        prefix = "IP-CIDR6" if net.version == 6 else "IP-CIDR"
        base = f"{prefix},{net},"
        return f"{base}{'no-resolve' if no_resolve else 'DIRECT'}"

    def payload(self, nets: list[Network], no_resolve: bool) -> list[str]:
        return [self.line(n, no_resolve) for n in nets]


@dataclass
class ClashFormat(Format):
    no_resolve: bool = False

    def line(self, net: Network, no_resolve: bool) -> str:
        prefix = "IP-CIDR6" if net.version == 6 else "IP-CIDR"
        base = f"{prefix},{net},"
        return f"  - {base}{'no-resolve' if no_resolve else 'DIRECT'}"

    def payload(self, nets: list[Network], no_resolve: bool) -> list[str]:
        return ["payload:"] + [self.line(n, no_resolve) for n in nets]


@dataclass
class QuantumultXFormat(Format):
    no_resolve: bool = False

    def line(self, net: Network, no_resolve: bool) -> str:
        prefix = "ip6-cidr" if net.version == 6 else "ip-cidr"
        return f"{prefix}, {net}"

    def payload(self, nets: list[Network], no_resolve: bool) -> list[str]:
        return [self.line(n, no_resolve) for n in nets]


@dataclass
class LoonFormat(Format):
    no_resolve: bool = False

    def line(self, net: Network, no_resolve: bool) -> str:
        prefix = "IP-CIDR6" if net.version == 6 else "IP-CIDR"
        base = f"{prefix},{net},"
        return f"{base}{'no-resolve' if no_resolve else 'DIRECT'}"

    def payload(self, nets: list[Network], no_resolve: bool) -> list[str]:
        return [self.line(n, no_resolve) for n in nets]


def build_formats() -> dict[str, Format]:
    """构造格式注册表（顺序即输出优先级）。"""
    surge = SurgeFormat("Surge", ".list", "Surge")
    clash = ClashFormat("Clash", ".yaml", "Clash")
    qu = QuantumultXFormat("QuantumultX", ".txt", "QuantumultX")
    loon = LoonFormat("Loon", ".list", "Loon")
    return {f.name: f for f in (surge, clash, qu, loon)}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def header_lines(
    fmt: Format,
    cc: str,
    nets: list[Network],
    source_name: str,
    generated_at: str | None = None,
) -> list[str]:
    ipv4 = sum(1 for n in nets if n.version == 4)
    ipv6 = sum(1 for n in nets if n.version == 6)
    lines = [
        "# ============================================================",
        f"# Rule-Provider: {cc}",
        f"# {name_zh(cc)}（{name_en(cc)}）",
        f"# 数据日期: {generated_at or _now_utc()}",
        f"# 数据来源: {source_name}",
        f"# 规则统计: 共 {len(nets)} 条（IPv4: {ipv4} / IPv6: {ipv6}）",
        "# ============================================================",
    ]
    return lines


def render_rule_set(
    fmt: Format,
    cc: str,
    nets: list[Network],
    source_name: str,
    no_resolve: bool,
    generated_at: str | None = None,
) -> str:
    lines: list[str] = header_lines(fmt, cc, nets, source_name, generated_at)
    lines.extend(fmt.payload(nets, no_resolve))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# site（域名）规则渲染
# ---------------------------------------------------------------------------

DOMAIN_PREFIX = {
    "Surge": {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN", "keyword": "DOMAIN-KEYWORD", "regexp": "DOMAIN-REGEX"},
    "Clash": {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN", "keyword": "DOMAIN-KEYWORD", "regexp": "DOMAIN-REGEX"},
    "QuantumultX": {"domain": "host-suffix", "full": "host", "keyword": "host-keyword", "regexp": "host-regex"},
    "Loon": {"domain": "DOMAIN-SUFFIX", "full": "DOMAIN", "keyword": "DOMAIN-KEYWORD", "regexp": "DOMAIN-REGEX"},
}

_KIND_LABELS = {
    "domain": "域名",
    "full": "精确域名",
    "keyword": "关键字",
    "regexp": "正则",
}


def site_rule_line(fmt: Format, kind: str, value: str) -> str:
    prefix = DOMAIN_PREFIX[fmt.name][kind]
    if fmt.name == "Clash":
        return f"  - {prefix},{value}"
    if fmt.name == "QuantumultX":
        return f"{prefix}, {value}"
    return f"{prefix},{value}"


def site_header_lines(
    fmt: Format,
    cc: str,
    rules: list,
    source_name: str,
    generated_at: str | None = None,
) -> list[str]:
    counts: dict[str, int] = {}
    for r in rules:
        kind = r[0] if isinstance(r, tuple) else r.kind
        counts[kind] = counts.get(kind, 0) + 1
    by = " / ".join(
        f"{_KIND_LABELS[k]}: {counts[k]}" for k in ("domain", "full", "keyword", "regexp") if counts.get(k)
    )
    return [
        "# ============================================================",
        f"# Rule-Provider: {cc}",
        f"# {name_zh(cc)}（{name_en(cc)}）",
        f"# 数据日期: {generated_at or _now_utc()}",
        f"# 数据来源: {source_name}",
        f"# 规则统计: 共 {len(rules)} 条（{by}）",
        "# ============================================================",
    ]


def render_site_rule_set(
    fmt: Format,
    cc: str,
    rules: list,
    source_name: str,
    generated_at: str | None = None,
) -> str:
    lines: list[str] = site_header_lines(fmt, cc, rules, source_name, generated_at)
    if fmt.name == "Clash":
        lines.append("payload:")
    lines.extend(site_rule_line(fmt, r[0], r[1]) if isinstance(r, tuple) else site_rule_line(fmt, r.kind, r.value) for r in rules)
    return "\n".join(lines) + "\n"


def render_site_metadata(
    countries: dict[str, list],
    source_name: str,
    generated_at: str | None = None,
    global_rules: list | None = None,
) -> str:
    """生成 ruleset/site/metadata.json。"""
    payload: dict[str, dict] = {}
    for cc in sorted(countries):
        rules = countries[cc]
        counts: dict[str, int] = {}
        for r in rules:
            kind = r[0] if isinstance(r, tuple) else r.kind
            counts[kind] = counts.get(kind, 0) + 1
        payload[cc] = {
            "name_en": name_en(cc),
            "name_zh": name_zh(cc),
            "total_rules": len(rules),
            "rules": counts,
        }
    doc = {
        "type": "site",
        "generated_at": generated_at or _now_utc(),
        "source": source_name,
        "country_count": len(payload),
        "global_provider": (
            {"total_rules": len(global_rules)} if global_rules is not None else None
        ),
        "countries": payload,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def render_metadata(
    countries: dict[str, list[Network]],
    source_name: str,
    generated_at: str | None = None,
    global_nets: list[Network] | None = None,
) -> str:
    """生成 ruleset/metadata.json。"""
    payload: dict[str, dict] = {}
    for cc in sorted(countries):
        nets = countries[cc]
        ipv4 = sum(1 for n in nets if n.version == 4)
        ipv6 = sum(1 for n in nets if n.version == 6)
        payload[cc] = {
            "name_en": name_en(cc),
            "name_zh": name_zh(cc),
            "ipv4_cidrs": ipv4,
            "ipv6_cidrs": ipv6,
            "total_cidrs": len(nets),
        }
    doc = {
        "generated_at": generated_at or _now_utc(),
        "source": source_name,
        "country_count": len(payload),        "global_provider": (
            {
                "total_cidrs": len(global_nets),
                "ipv4_cidrs": sum(1 for n in global_nets if n.version == 4),
                "ipv6_cidrs": sum(1 for n in global_nets if n.version == 6),
            }
            if global_nets is not None
            else None
        ),
        "countries": payload,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def write_rule_set(path: Path, content: str) -> None:
    """原子写入：先写临时文件再重命名，避免生成中断产生半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
