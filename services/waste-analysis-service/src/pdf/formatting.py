def duration_label(seconds) -> str:
    if seconds is None:
        return "—"
    try:
        mins = max(0, int(round(float(seconds) / 60.0)))
    except Exception:
        return "—"
    hours = mins // 60
    rem = mins % 60
    if hours > 0:
        return f"{hours} hr {rem} min"
    return f"{rem} min"
