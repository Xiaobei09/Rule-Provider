#!/usr/bin/env python3
"""零依赖测试运行器（同时兼容 pytest 风格测试文件）。

用法：
    python3 run_tests.py [tests/test_xxx.py ...]    # 默认运行全部

在 CI（GitHub Actions）中使用：
    pip install pytest && pytest tests/
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent / "tests"


def load_module(path: Path):
    name = f"_rp_tests_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    args = sys.argv[1:]
    if args:
        files = [Path(a) if Path(a).is_absolute() else TESTS_DIR / a for a in args]
    else:
        files = sorted(TESTS_DIR.glob("test_*.py"))
    passed = failed = 0
    failures: list[str] = []
    for path in files:
        if not path.exists():
            print(f"[!] 未找到测试文件: {path}")
            return 1
        mod = load_module(path)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  [PASS] {path.stem}::{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append(f"{path.name}::{name}")
                print(f"  [FAIL] {path.name}::{name}")
                traceback.print_exc()
    print(f"\n结果: {passed} 通过, {failed} 失败")
    if failures:
        print("失败用例:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
