"""geosite（域名）数据模块。

数据源：v2fly/domain-list-community（互联网公开仓库，MIT 许可）
- data/<name>：分类域名列表，支持 include:/full:/domain:/keyword:/regexp:/attribute:
- data/tld-!cn：全球 ccTLD 清单（带国家名注释），用于「某国域名放入该国规则集」
- data/tld-cn / data/tld-ru：中国 / 俄罗斯 TLD 清单（含公司新 gTLD 与 IDN）

解析约束（详见 DEVELOPMENT.md「数据源」）：
1. 仅保留 domain（含裸域名，即后缀匹配）、full（精确）、keyword（关键字）、regexp 四类规则；
2. include: 需递归解析，循环引用以 seen 集合防护；
3. 规则后的 `@attribute` 一律剥离（属性用于路由偏好，不影响国家归属）；
4. 排除 attribute:/ext:/country: 等非规则行；
5. 输出必须稳定排序并去重。
"""

from __future__ import annotations

import re
import tarfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from countries import COUNTRIES

GEOSITE_ARCHIVE_URL = (
    "https://codeload.github.com/v2fly/domain-list-community/tar.gz/refs/heads/master"
)

RULE_KINDS = ("domain", "full", "keyword", "regexp")
RULE_ORDER = {kind: i for i, kind in enumerate(RULE_KINDS)}

# v2fly tld-!cn 未列出、但 IANA 已委派并在使用的 ccTLD 补全清单
# （来源：https://data.iana.org/TLD/tlds-alpha-by-domain.txt，2026-08 核验）
FALLBACK_CCTLD: dict[str, list[str]] = {
    "HK": ["hk"],
    "KP": ["kp"],
    "CO": ["co"],
    "FO": ["fo"],
    "FM": ["fm"],
    "GQ": ["gq"],
    "ME": ["me"],
    "MK": ["mk"],
    "PW": ["pw"],
    "RE": ["re"],
    "SZ": ["sz"],
    "TT": ["tt"],
    "TV": ["tv"],
    "TF": ["tf"],
    "CC": ["cc"],
}


@dataclass(frozen=True)
class Rule:
    kind: str  # domain | full | keyword | regexp
    value: str


def merge_rules(rules: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """按 (kind, value) 去重并按 RULE_ORDER 稳定排序。

    保证每个国家/Global 规则集中同一规则只出现一次（唯一性不变量）。
    """
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    for r in rules:
        seen.setdefault(r, r)
    return sorted(seen.values(), key=lambda r: (RULE_ORDER[r[0]], r[1]))


def load_archive(path: Path) -> dict[str, str]:
    """读取 v2fly 数据压缩包，返回 {文件名: 内容}。"""
    prefix = "domain-list-community-master/data/"
    out: dict[str, str] = {}
    try:
        with tarfile.open(path, "r:gz") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = member.name
                if not name.startswith(prefix) or "/" in name[len(prefix):]:
                    continue
                data = tf.extractfile(member)
                if data is None:
                    continue
                out[name[len(prefix):]] = data.read().decode("utf-8", errors="replace")
    except (tarfile.TarError, OSError) as exc:
        raise ValueError(f"无法读取 geosite 压缩包 {path}: {exc}") from exc
    return out


def _split_rule_line(line: str) -> str | None:
    """剥离注释与属性，返回规则 token 或 None。"""
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    token = line.split()[0].strip()
    if not token:
        return None
    return token


def resolve_category(data: dict[str, str], name: str) -> list[Rule]:
    """递归解析一个分类（含 include:），返回规则列表。"""
    rules: list[Rule] = []
    seen: set[str] = set()
    missing: set[str] = set()

    def _walk(current: str, stack: set[str]) -> None:
        if current in stack or current in seen:
            return
        text = data.get(current)
        if text is None:
            missing.add(current)
            return
        seen.add(current)
        for raw in text.splitlines():
            token = _split_rule_line(raw)
            if not token:
                continue
            if token.startswith("include:"):
                _walk(token[len("include:"):].strip(), stack | {current})
                continue
            for kind in RULE_KINDS:
                if token.startswith(kind + ":"):
                    rules.append(Rule(kind, token[len(kind) + 1:].strip()))
                    break
            else:
                # 裸域名 = 后缀匹配
                rules.append(Rule("domain", token))

    _walk(name, set())
    if missing:
        print(f"[geosite] 警告: 分类 {name} 引用缺失 {sorted(missing)}")
    return rules


# ---------------------------------------------------------------------------
# ccTLD -> 国家 映射
# ---------------------------------------------------------------------------

_STOPWORDS = {"OF", "THE", "AND", "REPUBLIC"}


def _normalize(text: str) -> str:
    """大写 + 去重音 + 标点归空格，便于国家名匹配。"""
    text = text.replace("\u2019", " ").replace("'", " ")
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(ch)
    )
    text = re.sub(r"[,.\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip().upper()


_NAME_TO_CC: dict[str, str] = {
    _normalize(en): cc for cc, (en, _zh) in COUNTRIES.items()
}

_ALIASES: dict[str, str] = {
    "ASCENSION ISLAND": "SH",
    "BOLIVIA": "BO",
    "BRUNEI": "BN",
    "CAPE VERDE": "CV",
    "CONGO": "CG",
    "CONGO DEMOCRATIC REPUBLIC OF THE": "CD",
    "CONGO THE DEMOCRATIC REPUBLIC OF THE": "CD",
    "CONGO REPUBLIC OF THE": "CG",
    "COTE D IVOIRE": "CI",
    "CZECH REPUBLIC": "CZ",
    "CZECHIA": "CZ",
    "EAST TIMOR": "TL",
    "EUROPEAN UNION": "EU",
    "FALKLAND ISLANDS MALVINAS": "FK",
    "GABONESE REPUBLIC": "GA",
    "GAMBIA": "GM",
    "GUERNSEY": "GG",
    "HOLY SEE": "VA",
    "HONG KONG": "HK",
    "IRAN": "IR",
    "IRAN ISLAMIC REPUBLIC OF": "IR",
    "IVORY COAST": "CI",
    "JERSEY": "JE",
    "KOREA": "KR",
    "KOREA REPUBLIC OF": "KR",
    "KOREA SOUTH": "KR",
    "LAOS": "LA",
    "LAO PEOPLE'S DEMOCRATIC REPUBLIC": "LA",
    "LIBYA": "LY",
    "MACAU": "MO",
    "MACEDONIA": "MK",
    "MOLDOVA": "MD",
    "NORTH KOREA": "KP",
    "REPUBLIC OF KOREA": "KR",
    "RUSSIA": "RU",
    "RUSSIAN FEDERATION": "RU",
    "SAINT HELENA": "SH",
    "SOUTH KOREA": "KR",
    "SYRIA": "SY",
    "TAIPEI REPUBLIC OF CHINA": "TW",
    "TAIWAN": "TW",
    "TAIWAN REPUBLIC OF CHINA": "TW",
    "TANZANIA": "TZ",
    "TANZANIA UNITED REPUBLIC OF": "TZ",
    "TIMOR-LESTE": "TL",
    "UNITED KINGDOM": "GB",
    "UNITED KINGDOM UK": "GB",
    "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND": "GB",
    "UNITED STATES": "US",
    "UNITED STATES OF AMERICA": "US",
    "UNITED STATES OF AMERICA USA": "US",
    "VATICAN CITY": "VA",
    "VENEZUELA": "VE",
    "VENEZUELA BOLIVARIAN REPUBLIC OF": "VE",
    "VIETNAM": "VN",
    "WALLIS AND FUTUNA": "WF",
    "WESTERN SAHARA": "EH",
}


def _comment_to_cc(comment: str) -> str | None:
    """从 tld-!cn 的国家注释解析出 ISO-2 代码（含别名）。"""
    tail = re.match(r"^(.*?)\s*\(([A-Za-z]{2,3})\)$", comment.strip())
    core = comment.strip()
    code = None
    if tail:
        core, code = tail.group(1), tail.group(2).upper()
    else:
        m = re.match(r"^(.*?)\s*\(.*\)$", comment.strip())
        if m:
            core = m.group(1)
    name = _normalize(core)
    cc = _ALIASES.get(name)
    if not cc:
        cc = _NAME_TO_CC.get(name)
    if cc:
        return cc
    # 尾部代码恰好是名称单词首字母缩写（如 USA/UK/UAE）时才采用
    if code:
        initials = "".join(
            w[0] for w in re.split(r"[\s,]+", core) if w and w.upper() not in _STOPWORDS
        ).upper()
        if initials == code:
            return code
    return None


def build_cc_tld_map(tld_all_text: str) -> dict[str, list[str]]:
    """解析 tld-!cn 文本 -> {CC: [tld, ...]}。忽略无国家注释的通用 TLD。"""
    out: dict[str, list[str]] = defaultdict(list)
    for line in tld_all_text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("include:"):
            continue
        m = re.match(r"^(\S+)\s+#\s*(.+)$", s)
        if not m:
            continue
        tld = m.group(1).lower()
        cc = _comment_to_cc(m.group(2))
        if cc:
            out[cc].append(tld)
    return dict(sorted(out.items()))


def parse_tld_list(text: str) -> list[str]:
    """解析 tld-cn / tld-ru 等 TLD 清单（裸 TLD 名）。"""
    out: list[str] = []
    for line in text.splitlines():
        token = _split_rule_line(line)
        if not token or token.startswith("include:"):
            continue
        out.append(token.lower())
    return out
