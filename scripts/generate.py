#!/usr/bin/env python3
"""Rule-Provider 生成器 CLI。

用法（在仓库根目录执行）：
    python scripts/generate.py fetch              # 从互联网下载并解析数据源到缓存
    python scripts/generate.py generate           # 根据缓存数据生成全部规则集
    python scripts/generate.py all                # fetch + generate
    python scripts/generate.py validate           # 校验已生成的规则集

常用选项：
    --source apnic|maxmind|auto   数据源选择（默认按配置）
    --formats Clash,Surge         只生成指定格式
    --countries CN,US,JP          只生成指定国家（ISO 3166-1 alpha-2）
    --no-global                   不生成全球 Rule Provider
    --no-site                     跳过 site（域名）规则集生成
    --no-merge                    关闭相邻段合并（调试用）
    --print-stats                 打印各国统计摘要

生成内容：
    ruleset/geoip/     每国 IP 段规则集（RIR + 可选 IPtoASN/MaxMind 并集，CIDR）
    ruleset/global/    全球 IP 段 Rule Provider（并集）
    ruleset/site/      每国域名规则集（ccTLD + v2fly geosite 分类 + 可选 top sites）
    ruleset/metadata.json / ruleset/site/metadata.json  元数据
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from cidr import count_ips, filter_reserved, merge_networks
from countries import name_zh
from sources import (
    SourceError,
    delegated_date,
    env_license_key,
    fetch_geosite,
    fetch_maxmind,
    fetch_rir,
    load_from_cache,
    parse_delegated,
    parse_maxmind_zip,
)
from config import DEFAULT_CACHE, DEFAULT_CONFIG, load_config
from render import (
    build_formats,
    render_metadata,
    render_rule_set,
    render_site_metadata,
    render_site_rule_set,
    write_rule_set,
)

ROOT = Path(__file__).resolve().parent.parent


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fetch(cfg: dict, source: str, cache_dir: Path) -> dict[str, list]:
    """下载并解析数据源，返回 {cc: [networks]}。"""
    net = cfg.get("network", {})
    timeout = int(net.get("timeout_seconds", 60))
    ua = net.get("user_agent", "Rule-Provider/1.0")

    rir_cfg = cfg["sources"]["rir"]
    rir_urls = rir_cfg.get("urls", [])
    statuses = rir_cfg.get("statuses", ["allocated", "assigned"])

    combined: dict[str, list] = {}
    total_all = used_all = 0
    paths = fetch_rir(rir_urls, cache_dir, timeout, ua)
    for url, path in paths.items():
        text = path.read_text(encoding="utf-8", errors="replace")
        nets, total, used = parse_delegated(text, statuses)
        total_all += total
        used_all += used
        for cc, v in nets.items():
            combined.setdefault(cc, []).extend(v)
        print(f"[fetch] {path.name}: 记录 {total} 条，采用 {used} 条")
    print(
        f"[fetch] RIR 合计: {len(rir_urls)} 个区域，采用 {used_all} 条，"
        f"覆盖 {len(combined)} 个国家/地区"
    )

    mm_cfg = cfg["sources"]["maxmind"]
    use_mm = mm_cfg.get("enabled", False) and source in ("auto", "maxmind")
    if source == "maxmind":
        use_mm = True
    if use_mm:
        key = env_license_key()
        if not key:
            print("[fetch] 警告: 未设置 MAXMIND_LICENSE_KEY，跳过 GeoLite2")
        else:
            mm_file = cache_dir / "GeoLite2-Country-CSV.zip"
            fetch_maxmind(mm_file, key, timeout, ua)
            mm_nets = parse_maxmind_zip(mm_file.read_bytes())
            for cc, nets in mm_nets.items():
                combined.setdefault(cc, []).extend(nets)
            print(f"[fetch] GeoLite2: 覆盖 {len(mm_nets)} 个国家/地区")

    ia = cfg["sources"].get("ip2asn", {})
    if ia.get("enabled", False):
        from iptoasn import fetch_ip2asn

        date = fetch_ip2asn(ia, cache_dir, timeout, ua)
        print(f"[fetch] IPtoASN: 已下载（数据日期 {date or '未知'}）")

    gs = cfg["sources"].get("geosite", {})
    if gs.get("enabled", True):
        from geosite import GEOSITE_ARCHIVE_URL

        gs_url = gs.get("url", GEOSITE_ARCHIVE_URL)
        gs_name = gs.get("archive", "geosite-v2fly.tar.gz")
        fetch_geosite(cache_dir / gs_name, gs_url, timeout, ua)
        print(f"[fetch] geosite: 已下载 {gs_name}")

    ts = cfg["sources"].get("top_sites", {})
    if ts.get("enabled", False):
        from topsites import fetch_top_sites

        fetch_top_sites(ts, cache_dir, timeout, ua)
    return combined


def _generate(
    cfg: dict, countries_nets: dict[str, list], source_name: str, args, generated_at: str
) -> None:
    rules = cfg.get("rules", {})
    no_resolve = bool(rules.get("no_resolve", True))
    filter_res = bool(rules.get("filter_reserved", True))
    include_v6 = bool(rules.get("include_ipv6", True))
    merge = bool(rules.get("merge_adjacent", True)) and not args.no_merge
    global_provider = bool(cfg.get("global_provider", True)) and not args.no_global

    formats = build_formats()
    selected = args.formats or cfg.get("formats", ["Surge", "Clash", "QuantumultX", "Loon"])
    selected = [f for f in selected if f in formats]
    if not selected:
        raise SystemExit("错误: 没有可用的输出格式")

    ruleset_dir = ROOT / cfg.get("ruleset_dir", "ruleset")
    geoip_dir = ruleset_dir / "geoip"

    # 国家筛选
    forced = list(cfg.get("include_extra", []))
    if args.countries:
        wanted = set(args.countries)
    else:
        wanted = None
    exclude = set(cfg.get("exclude", []))

    # 逐国家处理：过滤保留段 -> 合并 -> 渲染
    final: dict[str, list] = {}
    for cc in sorted(countries_nets):
        if wanted is not None and cc not in wanted and cc not in forced:
            continue
        if cc in exclude:
            continue
        nets = countries_nets[cc]
        if filter_res:
            nets = filter_reserved(nets)
        if not include_v6:
            nets = [n for n in nets if n.version == 4]
        if merge:
            nets = merge_networks(nets)
        nets.sort(key=lambda n: (n.version, str(n)))
        if not nets:
            continue
        final[cc] = nets

    # 全球 Rule Provider（全部国家并集）
    global_nets: list = []
    if global_provider:
        for nets in final.values():
            global_nets.extend(nets)
        if merge:
            global_nets = merge_networks(global_nets)
        global_nets.sort(key=lambda n: (n.version, str(n)))

    generated_at = generated_at or _utc()
    written = 0
    expected: set[Path] = set()
    for cc, nets in final.items():
        for name in selected:
            fmt = formats[name]
            out_dir = geoip_dir / fmt.dir_name
            out_file = out_dir / f"{cc}{fmt.ext}"
            write_rule_set(
                out_file,
                render_rule_set(fmt, cc, nets, source_name, no_resolve, generated_at),
            )
            written += 1
            expected.add(out_file)

    if global_provider:
        for name in selected:
            fmt = formats[name]
            out_dir = ruleset_dir / "global" / fmt.dir_name
            out_file = out_dir / f"Global{fmt.ext}"
            write_rule_set(
                out_file,
                render_rule_set(fmt, "Global", global_nets, source_name, no_resolve, generated_at),
            )
            written += 1
            expected.add(out_file)

    metadata_file = ruleset_dir / "metadata.json"
    write_rule_set(metadata_file, render_metadata(final, source_name, generated_at, global_nets))
    written += 1
    expected.add(metadata_file)

    # 清理不再属于本次生成集合的过期文件（如历史遗留、被排除的国家）
    removed = 0
    for out_dir in (geoip_dir, ruleset_dir / "global"):
        if not out_dir.exists():
            continue
        for old in sorted(out_dir.rglob("*")):
            if old.is_file() and old not in expected and old.name != "metadata.json":
                old.unlink()
                removed += 1
    if removed:
        print(f"[generate] 清理过期文件 {removed} 个")

    print(f"[generate] 生成 {len(final)} 个国家/地区 + {'全球' if global_provider else ''} 共 {written} 个文件 -> {ruleset_dir}")

    if args.print_stats:
        print(f"\n{'代码':<4} {'中文名':<20} {'IPv4段':>8} {'IPv6段':>8} {'IP总数':>12}")
        print("-" * 60)
        for cc in sorted(final):
            nets = final[cc]
            ipv4 = sum(1 for n in nets if n.version == 4)
            ipv6 = sum(1 for n in nets if n.version == 6)
            print(f"{cc:<4} {name_zh(cc):<20} {ipv4:>8} {ipv6:>8} {count_ips(nets):>12}")


def _generate_site(
    cfg: dict, archive: Path, cache_dir: Path, source_name: str, args, generated_at: str
) -> None:
    """根据 v2fly geosite 数据（+ 可选 top sites 补充）生成各国域名规则集。"""
    from geosite import (
        FALLBACK_CCTLD,
        build_cc_tld_map,
        load_archive,
        merge_rules,
        prune_covered_rules,
        resolve_category,
    )

    if not archive.exists():
        print(f"[site] 警告: 缺少 geosite 缓存 {archive.name}，跳过 site 生成")
        return
    data = load_archive(archive)
    tld_all = data.get("tld-!cn", "")
    if not tld_all:
        print("[site] 警告: geosite 缓存中无 tld-!cn，跳过 site 生成")
        return

    cc_tld_map = build_cc_tld_map(tld_all)
    for cc, tlds in FALLBACK_CCTLD.items():
        cc_tld_map.setdefault(cc, []).extend(tlds)

    # categories 值支持单个分类名或分类名列表（如应用分类归属其母国）
    raw_categories = cfg.get("sources", {}).get("geosite", {}).get("categories", {})
    categories: dict[str, list[str]] = {}
    for cc, name in raw_categories.items():
        names = name if isinstance(name, list) else [name]
        categories[cc.upper()] = [str(n) for n in names if n]
    for cc in categories:
        cc_tld_map.setdefault(cc, [])

    forced = list(cfg.get("include_extra", []))
    wanted = set(args.countries) if args.countries else None
    exclude = set(cfg.get("exclude", []))
    global_provider = bool(cfg.get("global_provider", True)) and not args.no_global

    # top sites 补充：读取缓存并筛出本次生成的目标国家
    top_sites: dict[str, list[str]] = {}
    ts_cfg = cfg.get("sources", {}).get("top_sites", {})
    if ts_cfg.get("enabled", False):
        from topsites import load_top_sites

        cached = load_top_sites(cache_dir)
        for cc in sorted(cached):
            if wanted is not None and cc not in wanted and cc not in forced:
                continue
            if cc in exclude:
                continue
            top_sites[cc] = cached[cc]
        if top_sites:
            print(f"[site] 补充热门网站域名: {len(top_sites)} 个国家")
        else:
            print("[site] 警告: top_sites 已启用但无缓存数据，请先执行 fetch")

    site_source = source_name
    if top_sites:
        site_source = source_name + " + CrUX top sites"

    per_country: dict[str, list] = {}
    for cc in sorted(cc_tld_map):
        if wanted is not None and cc not in wanted and cc not in forced:
            continue
        if cc in exclude:
            continue
        rules = [("domain", t) for t in cc_tld_map[cc]]
        for cat in categories.get(cc, []):
            rules.extend((r.kind, r.value) for r in resolve_category(data, cat))
        if cc in top_sites:
            tlds = cc_tld_map[cc]
            rules.extend(
                ("domain", d)
                for d in top_sites[cc]
                if not any(d == t or d.endswith("." + t) for t in tlds)
            )
        rules = merge_rules(rules)
        rules = prune_covered_rules(rules)
        if not rules:
            continue
        per_country[cc] = rules

    global_rules: list = []
    if global_provider:
        for rules in per_country.values():
            global_rules.extend(rules)
        global_rules = prune_covered_rules(merge_rules(global_rules))

    formats = build_formats()
    selected = args.formats or cfg.get("formats", ["Surge", "Clash", "QuantumultX", "Loon"])
    selected = [f for f in selected if f in formats]

    ruleset_dir = ROOT / cfg.get("ruleset_dir", "ruleset")
    site_dir = ruleset_dir / "site"
    generated_at = generated_at or _utc()
    written = 0
    expected: set[Path] = set()
    for cc, rules in per_country.items():
        for name in selected:
            fmt = formats[name]
            out_file = site_dir / fmt.dir_name / f"{cc}{fmt.ext}"
            write_rule_set(out_file, render_site_rule_set(fmt, cc, rules, site_source, generated_at))
            written += 1
            expected.add(out_file)

    if global_provider:
        for name in selected:
            fmt = formats[name]
            out_file = site_dir / fmt.dir_name / f"Global{fmt.ext}"
            write_rule_set(out_file, render_site_rule_set(fmt, "Global", global_rules, site_source, generated_at))
            written += 1
            expected.add(out_file)

    metadata_file = site_dir / "metadata.json"
    write_rule_set(metadata_file, render_site_metadata(per_country, site_source, generated_at, global_rules))
    written += 1
    expected.add(metadata_file)

    removed = 0
    if site_dir.exists():
        for old in sorted(site_dir.rglob("*")):
            if old.is_file() and old not in expected and old.name != "metadata.json":
                old.unlink()
                removed += 1
    if removed:
        print(f"[site] 清理过期文件 {removed} 个")

    print(
        f"[site] 生成 {len(per_country)} 个国家/地区 + {'全球' if global_provider else ''} "
        f"共 {written} 个文件 -> {site_dir}"
    )


def _validate(cfg: dict) -> int:
    """校验已生成文件：语法、排序、保留段、重复。返回错误数。"""
    import ipaddress
    import json

    rules = cfg.get("rules", {})
    no_resolve = bool(rules.get("no_resolve", True))
    include_v6 = bool(rules.get("include_ipv6", True))
    ruleset_dir = ROOT / cfg.get("ruleset_dir", "ruleset")
    formats = build_formats()
    errors = 0
    files = sorted(ruleset_dir.rglob("*"))
    cidr_prefixes = ("IP-CIDR", "IP-CIDR6", "ip-cidr", "ip6-cidr")
    domain_prefixes = {
        "Surge": ("DOMAIN-SUFFIX", "DOMAIN", "DOMAIN-KEYWORD", "DOMAIN-REGEX"),
        "Clash": ("DOMAIN-SUFFIX", "DOMAIN", "DOMAIN-KEYWORD", "DOMAIN-REGEX"),
        "QuantumultX": ("host-suffix", "host", "host-keyword", "host-regex"),
        "Loon": ("DOMAIN-SUFFIX", "DOMAIN", "DOMAIN-KEYWORD", "DOMAIN-REGEX"),
    }
    for path in files:
        if not path.is_file() or path.name == "metadata.json":
            continue
        is_site = "site" in path.parts
        fmt_name = next((p for p in formats if p in path.parts), None)
        valid_prefixes = domain_prefixes.get(fmt_name) if is_site else cidr_prefixes
        lines = [
            ln.strip()
            for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        if path.suffix == ".yaml" and lines:
            if lines[0] != "payload:":
                print(f"[validate] FAIL {path.name}: 首行应为 payload:")
                errors += 1
            lines = [ln.strip()[2:].strip() for ln in lines[1:]]
        seen: set[str] = set()
        suffix_set: set[str] = set()
        if is_site:
            suffix_set = {
                ln.split(",")[1].strip()
                for ln in lines
                if ln.startswith("DOMAIN-SUFFIX,")
            }
        for idx, ln in enumerate(lines, 1):
            parts = [p.strip() for p in ln.split(",")]
            if not ln.startswith(valid_prefixes):
                print(f"[validate] FAIL {path.name}:{idx} 非法前缀 -> {ln}")
                errors += 1
                continue
            if is_site:
                value = parts[1] if len(parts) > 1 else ""
                if not value:
                    print(f"[validate] FAIL {path.name}:{idx} 缺少域名 -> {ln}")
                    errors += 1
                    continue
                key = (parts[0], value)
                if key in seen:
                    print(f"[validate] FAIL {path.name}:{idx} 重复规则 -> {ln}")
                    errors += 1
                seen.add(key)
                if ln.startswith("DOMAIN-SUFFIX,") or ln.startswith("DOMAIN,"):
                    parents = [
                        ".".join(value.split(".")[i:])
                        for i in range(1, len(value.split(".")))
                    ]
                    covered = any(p in suffix_set for p in parents)
                    if ln.startswith("DOMAIN,") and value in suffix_set:
                        covered = True
                    if covered:
                        print(
                            f"[validate] FAIL {path.name}:{idx} "
                            f"被上级后缀覆盖(冗余) -> {ln}"
                        )
                        errors += 1
                continue
            if len(parts) < 2:
                print(f"[validate] FAIL {path.name}:{idx} 缺少 CIDR -> {ln}")
                errors += 1
                continue
            net_part = parts[1]
            try:
                net = ipaddress.ip_network(net_part, strict=False)
            except ValueError as exc:
                print(f"[validate] FAIL {path.name}:{idx} CIDR 非法 -> {net_part} ({exc})")
                errors += 1
                continue
            if no_resolve and path.suffix != ".txt":
                suffix_ok = parts[-1] in ("no-resolve", "DIRECT")
                if not suffix_ok:
                    print(f"[validate] FAIL {path.name}:{idx} 缺少策略后缀 -> {ln}")
                    errors += 1
            if net.version == 6 and not include_v6:
                print(f"[validate] FAIL {path.name}:{idx} 不应包含 IPv6 -> {ln}")
                errors += 1
            key = str(net)
            if key in seen:
                print(f"[validate] FAIL {path.name}:{idx} 重复 CIDR -> {ln}")
                errors += 1
            seen.add(key)
    print(f"[validate] 完成，错误 {errors} 个")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generate", description="Rule-Provider 生成器")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["fetch", "generate", "all", "validate"],
        default="all",
        help="fetch=下载数据; generate=生成规则; all=两者; validate=校验已生成文件",
    )
    parser.add_argument("--source", choices=["apnic", "maxmind", "auto"], default="auto")
    parser.add_argument("--formats", help="逗号分隔: Surge,Clash,QuantumultX,Loon")
    parser.add_argument("--countries", help="逗号分隔国家代码: CN,US,JP")
    parser.add_argument("--no-global", action="store_true")
    parser.add_argument("--no-site", action="store_true", help="跳过 site（域名）规则集生成")
    parser.add_argument("--no-merge", action="store_true")
    parser.add_argument("--print-stats", action="store_true")
    args = parser.parse_args(argv)
    if args.countries:
        args.countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    if args.formats:
        args.formats = [f.strip() for f in args.formats.split(",") if f.strip()]

    cfg = load_config(DEFAULT_CONFIG)
    cache_dir = ROOT / cfg.get("network", {}).get("cache_dir", DEFAULT_CACHE)

    source_name = "APNIC"
    if args.source == "auto" and cfg["sources"]["maxmind"].get("enabled"):
        source_name = "MaxMind GeoLite2 + APNIC"
    elif args.source == "maxmind":
        source_name = "MaxMind GeoLite2 + APNIC"
    if cfg["sources"].get("ip2asn", {}).get("enabled", False):
        source_name += " + IPtoASN"

    try:
        if args.command in ("fetch", "all"):
            _fetch(cfg, args.source, cache_dir)
        if args.command in ("generate", "all"):
            rir_cfg = cfg["sources"]["rir"]
            statuses = rir_cfg.get("statuses", ["allocated", "assigned"])
            countries_nets: dict[str, list] = {}
            data_dates: list[str] = []
            for url in rir_cfg.get("urls", []):
                name = url.rsplit("/", 1)[-1]
                path = cache_dir / name
                if not path.exists():
                    continue
                text = load_from_cache(path).decode("utf-8", errors="replace")
                d = delegated_date(text)
                if d:
                    data_dates.append(d)
                nets, _total, _used = parse_delegated(text, statuses)
                for cc, v in nets.items():
                    countries_nets.setdefault(cc, []).extend(v)
            if not countries_nets:
                print("缓存为空，先执行 fetch")
                _fetch(cfg, args.source, cache_dir)
                for url in rir_cfg.get("urls", []):
                    name = url.rsplit("/", 1)[-1]
                    text = load_from_cache(cache_dir / name).decode("utf-8", errors="replace")
                    d = delegated_date(text)
                    if d:
                        data_dates.append(d)
                    nets, _t, _u = parse_delegated(text, statuses)
                    for cc, v in nets.items():
                        countries_nets.setdefault(cc, []).extend(v)
            countries_nets = {cc: merge_networks(v) for cc, v in countries_nets.items()}
            # IPtoASN 补充：与 RIR 并集（ASN 注册国，弥补分配国盲区）
            if cfg["sources"].get("ip2asn", {}).get("enabled", False):
                from iptoasn import load_date, load_ip2asn

                ip2asn_nets = load_ip2asn(cache_dir, cfg["sources"]["ip2asn"])
                for cc, nets in ip2asn_nets.items():
                    countries_nets.setdefault(cc, []).extend(nets)
                d = load_date(cache_dir)
                if d:
                    data_dates.append(d)
                if ip2asn_nets:
                    print(f"[generate] IPtoASN 补充: 覆盖 {len(ip2asn_nets)} 个国家/地区")
            countries_nets = {cc: merge_networks(v) for cc, v in countries_nets.items()}
            # 以 RIR 数据日期作为快照标识：同日数据重复生成输出一致（幂等）
            generated_at = max(data_dates, default=_utc())
            _generate(cfg, countries_nets, source_name, args, generated_at)
            if not args.no_site and cfg["sources"].get("geosite", {}).get("enabled", True):
                gs = cfg["sources"]["geosite"]
                gs_name = gs.get("archive", "geosite-v2fly.tar.gz")
                _generate_site(
                    cfg,
                    cache_dir / gs_name,
                    cache_dir,
                    "v2fly domain-list-community",
                    args,
                    generated_at,
                )
        if args.command == "validate":
            return _validate(cfg)
    except SourceError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
