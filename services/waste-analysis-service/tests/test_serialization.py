import math

from src.utils.serialization import clean_for_json


def test_clean_for_json_recursive_nan_inf():
    payload = {
        "a": float("nan"),
        "b": [1.0, float("inf"), {"c": -float("inf")}],
        "d": {"nested": 2.0},
    }
    out = clean_for_json(payload)
    assert out["a"] is None
    assert out["b"][1] is None
    assert out["b"][2]["c"] is None
    assert math.isclose(out["d"]["nested"], 2.0)
