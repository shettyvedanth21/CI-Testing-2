import math
from datetime import datetime, date
from typing import Any

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None


def clean_for_json(obj: Any) -> Any:
    """Recursively clean objects for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, date):
        return obj.isoformat()
    elif np is not None and isinstance(obj, np.generic):
        return clean_for_json(obj.item())
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [clean_for_json(item) for item in obj]
    return obj


def extract_engine_data(result: dict) -> dict:
    """Extract data from standardized engine response.
    
    Args:
        result: Engine response with "success" and optional "data" keys
        
    Returns:
        The data dict if success, empty dict if failure
    """
    if isinstance(result, dict) and result.get("success") is True:
        return result.get("data", {})
    return {}
