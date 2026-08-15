"""轻量 YAML 子集解析器（零第三方依赖）。

仅支持本仓库 config.yaml 所需的语法：映射、嵌套映射、缩进列表、内联列表、标量。
不做通用 YAML 兼容承诺（见 DEVELOPMENT.md）。
"""

from __future__ import annotations

import re
from pathlib import Path

_BOOL = {"true": True, "false": False, "yes": True, "no": False}
_INT = re.compile(r"^-?\d+$")
_INLINE_COMMENT = re.compile(r"^(.*?)(\s+#.*)$")


class _Lazy:
    """空值节点：根据后续子项类型解析为 dict 或 list。"""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = None  # type: ignore[assignment]


def _strip_comment(line: str) -> str:
    line = line.rstrip()
    if not line:
        return ""
    if line.lstrip().startswith("#"):
        return ""
    m = _INLINE_COMMENT.match(line)
    if m:
        return m.group(1).rstrip()
    return line


def _parse_scalar(raw: str):
    v = raw.strip()
    if v == "":
        return None
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x) for x in inner.split(",") if x.strip()]
    if _INT.match(v):
        return int(v)
    if v in _BOOL:
        return _BOOL[v]
    if (v.startswith('"') and v.endswith('"')) or (
        v.startswith("'") and v.endswith("'")
    ):
        return v[1:-1]
    return v


def _resolve(node, as_list: bool):
    """把 _Lazy 按上下文解析为 dict 或 list。"""
    if node is None:
        return [] if as_list else {}
    if isinstance(node, _Lazy):
        if node.value is None:
            node.value = [] if as_list else {}  # type: ignore[assignment]
        return node.value
    return node


def load_yaml(path: Path | str) -> dict:
    lines = [_strip_comment(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines()]
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]
    for lineno, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()

        if content.startswith("- "):
            item = _parse_scalar(content[2:])
            while len(stack) > 1 and indent <= stack[-1][0]:
                stack.pop()
            _, parent = stack[-1]
            container = _resolve(parent, as_list=True)
            if not isinstance(container, list):
                raise ValueError(f"配置格式错误 第{lineno}行: 列表项不在列表中")
            container.append(item)
            continue

        if ":" not in content:
            raise ValueError(f"配置格式错误 第{lineno}行: 缺少冒号 -> {raw!r}")
        key, _, val = content.partition(":")
        key = key.strip()
        if not key:
            raise ValueError(f"配置格式错误 第{lineno}行: 空键名")
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        _, parent = stack[-1]
        container = _resolve(parent, as_list=False)
        if isinstance(container, list):
            raise ValueError(f"配置格式错误 第{lineno}行: 列表内不允许键值对 -> {raw!r}")

        if val.strip() == "":
            child: object = _Lazy()
            container[key] = child
            stack.append((indent, child))
        else:
            container[key] = _parse_scalar(val)
    return _materialize(root)


def _materialize(node):
    """将残留的 _Lazy（空块）展开为 dict/list，保证返回值为纯 JSON 结构。"""
    if isinstance(node, _Lazy):
        return _materialize(node.value if node.value is not None else {})
    if isinstance(node, dict):
        return {k: _materialize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_materialize(v) for v in node]
    return node


DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / "cache"


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    cfg = load_yaml(path)
    if not isinstance(cfg, dict):
        raise ValueError("配置文件顶层必须是映射")
    return cfg
