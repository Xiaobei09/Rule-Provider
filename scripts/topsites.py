"""各国热门网站（top sites）补充数据模块。

数据源：InternetHealthReport/crux-top-lists-country
（https://github.com/InternetHealthReport/crux-top-lists-country，Chrome CrUX
按国家 Top 网站公开数据，每月更新）。每个国家的数据为 gzip CSV：

    origin,rank
    https://www.example.com,1000
    ...

- `rank` 是量级桶（1000 / 10000 / 100000 / 1000000），行按桶有序、桶内随机；
- `origin` 是完整 URL，需提取出可注册域名（eTLD+1）作为 `domain:`（后缀匹配）规则。

域名规范化使用 Public Suffix List（https://publicsuffix.org/list/，ICANN +
PRIVATE 两段），保证 `www.foo.co.uk -> foo.co.uk`、`a.blogspot.com -> a.blogspot.com`
这类划分正确。

下载策略（详见 DEVELOPMENT.md「top sites」）：
- rank<=10000 的行位于文件头部，用 HTTP Range 只取头部若干 KB 即可覆盖
  top_n（默认 5000）所需数据，避免整文件（约 8MB × 238 国）重复下载；
- gzip 流截断时只解压出已收到的部分（捕获 EOFError），解析结果以该部分为准；
- 最新月份通过一次 Git Tree API 调用获得，并随结果缓存。
"""

from __future__ import annotations

import gzip
import io
import json
import re
import urllib.request
from pathlib import Path
from typing import Callable

from sources import SourceError, _download

PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"
MONTHS_URL = (
    "https://api.github.com/repos/InternetHealthReport/crux-top-lists-country/"
    "git/trees/main?recursive=1"
)
DEFAULT_URL = (
    "https://raw.githubusercontent.com/InternetHealthReport/"
    "crux-top-lists-country/main/data/country/{cc}/{month}.csv.gz"
)

# 头部渐进下载：256KB 通常已覆盖 rank<=10000 全部行，不足时逐级放大
_RANGE_STEPS = (256 * 1024, 1024 * 1024, 4 * 1024 * 1024)
_HEAD_MIN_TEXT = 200_000  # 解压文本达到该长度即认为覆盖了 top-10000 头部
_DECOMPRESS_CAP = 8 * 1024 * 1024

_CACHE_NAME = "crux-top-sites.json"


class PublicSuffixList:
    """最小化 PSL：rules + exceptions（含通配 `*.foo` 规则）。"""

    def __init__(self, rules: set[str], exceptions: set[str]) -> None:
        self.rules = rules
        self.exceptions = exceptions

    def _matches(self, candidate: str) -> bool:
        if candidate in self.rules:
            return True
        dot = candidate.find(".")
        if dot < 0:
            return False
        return "*." + candidate[dot + 1:] in self.rules

    def _public_suffix_index(self, labels: list[str]) -> int | None:
        """返回公开后缀起始下标；异常规则 `!x.y` 视 `y` 为公开后缀。"""
        for i in range(len(labels)):
            candidate = ".".join(labels[i:])
            if candidate in self.exceptions:
                return i + 1
        for i in range(len(labels)):
            if self._matches(".".join(labels[i:])):
                return i
        return None

    def registrable_domain(self, host: str) -> str | None:
        """计算可注册域名（eTLD+1）；无法确定（IP/裸公后缀/未知后缀）返回 None。"""
        host = host.strip().rstrip(".").lower()
        if not host or "/" in host or " " in host or ":" in host:
            return None
        if re.fullmatch(r"[0-9.]+", host):
            return None
        labels = host.split(".")
        if len(labels) < 2:
            return None
        idx = self._public_suffix_index(labels)
        if idx is None or idx == 0:
            return None
        return ".".join(labels[idx - 1:])


def parse_psl(text: str) -> PublicSuffixList:
    """解析 PSL 文件文本（含 ICANN 与 PRIVATE 两段）。"""
    rules: set[str] = set()
    exceptions: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("!"):
            exceptions.add(line[1:].lower())
        else:
            rules.add(line.lower())
    return PublicSuffixList(rules, exceptions)


def latest_months(tree_text: str) -> dict[str, str]:
    """从 Git Tree JSON 计算每个国家最新数据月份，如 {cc: '202607'}。"""
    data = json.loads(tree_text)
    months: dict[str, str] = {}
    for item in data.get("tree", []):
        m = re.fullmatch(r"data/country/([a-z]{2})/(\d{6})\.csv\.gz", item.get("path", ""))
        if not m:
            continue
        cc, month = m.group(1), m.group(2)
        if cc not in months or month > months[cc]:
            months[cc] = month
    return dict(sorted(months.items()))


def _decompress_partial(data: bytes) -> str:
    """解压 gzip 数据；流被截断（Range 取头）时返回已解压部分。"""
    buf = io.BytesIO(data)
    out = bytearray()
    gf = gzip.GzipFile(fileobj=buf)
    while True:
        try:
            chunk = gf.read(65536)
        except (EOFError, OSError, gzip.BadGzipFile):
            break
        if not chunk:
            break
        out += chunk
        if len(out) > _DECOMPRESS_CAP:
            break
    return out.decode("utf-8", errors="replace")


def _read_range(
    req: urllib.request.Request, timeout: int, user_agent: str
) -> bytes:
    """带重试的 Range 读取；应对代理/网络偶发提前断开（IncompleteRead）。"""
    import http.client

    last: Exception | None = None
    for _ in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if not data:
                    continue
                return data
        except (OSError, http.client.IncompleteRead) as exc:
            last = exc
    raise SourceError(f"下载失败: {req.full_url} -> {last}")


def _fetch_head(url: str, timeout: int, user_agent: str) -> str:
    """只取文件头部并解压；文本不足时逐级放大 Range，最后整文件兜底。"""
    for limit in _RANGE_STEPS:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent, "Range": f"bytes=0-{limit - 1}"},
        )
        data = _read_range(req, timeout, user_agent)
        text = _decompress_partial(data)
        if len(text) >= _HEAD_MIN_TEXT:
            return text
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return _decompress_partial(data)


def _origin_host(origin: str) -> str | None:
    """从 origin URL 提取 host（去协议/路径/端口/用户信息）。"""
    host = origin.split("://", 1)[-1]
    host = host.split("/", 1)[0]
    host = host.split("@")[-1]
    host = host.split(":")[0]
    return host.lower() or None


def extract_domains(
    csv_text: str, psl: PublicSuffixList, top_n: int
) -> list[str]:
    """从 CrUX CSV 文本提取 top_n 个可注册域名。

    rank=1000 的行优先，随后按文件顺序取 rank=10000 的行，直至达到 top_n；
    重复域名只保留一条。
    """
    seen: set[str] = set()
    top1000: list[str] = []
    top10000: list[str] = []
    for raw in csv_text.splitlines():
        line = raw.strip()
        if not line or "," not in line:
            continue
        origin, _, rank = line.partition(",")
        if rank not in ("1000", "10000"):
            continue
        host = _origin_host(origin)
        if not host:
            continue
        domain = psl.registrable_domain(host)
        if domain is None or domain in seen:
            continue
        seen.add(domain)
        if rank == "1000":
            top1000.append(domain)
        else:
            top10000.append(domain)
    return (top1000 + top10000)[:top_n]


def fetch_top_sites(
    ts_cfg: dict, cache_dir: Path, timeout: int, user_agent: str
) -> dict[str, list[str]]:
    """下载 PSL、月份树并提取各国 top 域名，缓存到 cache/，返回 {cc: [domain]}。

    配置键：url（模板，含 {cc}/{month}）、months_url、psl_url、psl_archive、
    tree_archive、top_n。
    """
    psl_path = cache_dir / ts_cfg.get("psl_archive", "public_suffix_list.dat")
    _download(ts_cfg.get("psl_url", PSL_URL), psl_path, timeout, user_agent)
    psl = parse_psl(psl_path.read_text(encoding="utf-8", errors="replace"))

    tree_path = cache_dir / ts_cfg.get("tree_archive", "crux-tree.json")
    _download(ts_cfg.get("months_url", MONTHS_URL), tree_path, timeout, user_agent)
    months = latest_months(tree_path.read_text(encoding="utf-8", errors="replace"))

    top_n = int(ts_cfg.get("top_n", 5000))
    url_tpl = ts_cfg.get("url", DEFAULT_URL)
    payload: dict[str, dict] = {}
    for cc, month in months.items():
        url = url_tpl.format(cc=cc, month=month)
        try:
            text = _fetch_head(url, timeout, user_agent)
            domains = extract_domains(text, psl, top_n)
        except Exception as exc:  # noqa: BLE001
            print(f"[top-sites] 警告: {cc} 下载失败，跳过 -> {exc}")
            continue
        if not domains:
            continue
        payload[cc] = {"month": month, "domains": domains}
        print(f"[top-sites] {cc}: {len(domains)} 个域名（{month}）")

    doc = {"top_n": top_n, "countries": payload}
    cache_path = cache_dir / _CACHE_NAME
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache_path)
    print(f"[top-sites] 缓存 {len(payload)} 个国家 -> {cache_path}")
    return {cc.upper(): v["domains"] for cc, v in payload.items()}


def load_top_sites(cache_dir: Path) -> dict[str, list[str]]:
    """读取缓存的各国 top 域名；无缓存返回空字典。国家码统一大写。"""
    path = cache_dir / _CACHE_NAME
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {cc.upper(): v["domains"] for cc, v in doc.get("countries", {}).items()}
