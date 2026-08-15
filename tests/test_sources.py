"""sources 模块单元测试：RIR 数据解析。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from sources import parse_delegated


SAMPLE = """2|ripencc|20260814|508|19861004|20260814|+0000
arin|US|asn|1|1|19900101|allocated
apnic|CN|ipv4|1.0.0.0|256|20110414|allocated
apnic|CN|ipv4|1.0.1.0|256|20110414|allocated
ripencc|DE|ipv6|2001:4dd0::|32|20041207|allocated
ripencc|DE|ipv6|2001:4dd1::|32|20041207|allocated
arin|US|ipv4|8.8.8.0|256|20141205|assigned
lacnic|BR|ipv4|10.0.0.0|2048|20060822|allocated
apnic|XX|ipv4|14.102.240.0|4096||available
apnic||ipv4|27.0.8.0|1024||reserved
apnic|JP|ipv4|1.1.1.0|384|20110414|allocated
"""


def test_parse_basic():
    out, total, used = parse_delegated(SAMPLE, ["allocated", "assigned"])
    assert "CN" in out
    assert "DE" in out
    assert "US" in out
    assert "BR" in out


def test_parse_ignores_asn_available_empty():
    out, total, used = parse_delegated(SAMPLE, ["allocated", "assigned"])
    assert "AS" not in out  # asn 类型忽略
    assert "XX" not in out  # available 状态忽略
    assert "" not in out  # 空国家码忽略


def test_parse_status_filter():
    out, total, used = parse_delegated(SAMPLE, ["allocated"])
    assert "US" not in out  # assigned 状态应被过滤
    assert "CN" in out


def test_ipv4_raw_networks():
    out, _, _ = parse_delegated(SAMPLE, ["allocated", "assigned"])
    cn = {str(n) for n in out["CN"]}
    assert cn == {"1.0.0.0/24", "1.0.1.0/24"}


def test_ipv4_non_power_of_two():
    out, _, _ = parse_delegated(SAMPLE, ["allocated", "assigned"])
    jp = sorted(str(n) for n in out["JP"])
    # 384 = 256 + 128，按对齐拆分
    assert jp == ["1.1.1.0/24", "1.1.2.0/25"]


def test_ipv6_prefix():
    out, _, _ = parse_delegated(SAMPLE, ["allocated", "assigned"])
    de = sorted(str(n) for n in out["DE"])
    assert de == ["2001:4dd0::/32", "2001:4dd1::/32"]


def test_count_records():
    out, total, used = parse_delegated(SAMPLE, ["allocated", "assigned"])
    assert total == 11
    assert used == 7
