"""
Shared assertion helpers.
"""

from __future__ import annotations

import math
from typing import Any


def assert_no_nan_inf(obj: Any, path: str = "root"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            assert_no_nan_inf(value, path=f"{path}.{key}")
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            assert_no_nan_inf(value, path=f"{path}[{idx}]")
    elif isinstance(obj, float):
        assert not math.isnan(obj), f"NaN found at {path}"
        assert not math.isinf(obj), f"Infinity found at {path}"


def assert_valid_pdf(content: bytes, label: str = "PDF"):
    assert len(content) > 1000, f"{label} too small ({len(content)} bytes)"
    assert content[:4] == b"%PDF", f"{label} does not start with %PDF magic bytes"


def assert_numeric_non_negative(val, label: str = "value"):
    if val is None:
        return
    assert isinstance(val, (int, float)), f"{label} is not numeric: {type(val)} = {val}"
    assert val >= 0, f"{label} is negative: {val}"
