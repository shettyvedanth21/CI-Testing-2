import base64
from io import BytesIO
from typing import Any, Optional

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def _style_axes(ax):
    ax.set_facecolor("#ffffff")
    ax.figure.patch.set_facecolor("#f8fafc")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")
    ax.tick_params(colors="#334155")


def daily_energy_bar_chart(daily_series: list[dict], device_names: Optional[dict] = None) -> str:
    if not daily_series:
        return ""
    
    dates = [d.get("date", "") for d in daily_series]
    values = [float(d.get("kwh", 0) or 0) for d in daily_series]

    fig, ax = plt.subplots(figsize=(10, 4.6))
    cmap = plt.get_cmap("Blues")
    colors = [cmap(0.45 + (0.42 * i / max(len(values) - 1, 1))) for i in range(len(values))]
    bars = ax.bar(dates, values, color=colors, edgecolor="#1d4ed8", linewidth=0.8, alpha=0.96)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            height,
            f"{height:.1f}",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#0f172a",
        )

    if values:
        avg = float(np.mean(values))
        ax.axhline(avg, color="#14b8a6", linestyle="--", linewidth=1.4, alpha=0.95, label=f"Avg {avg:.2f} kWh")
    
    _style_axes(ax)
    ax.set_xlabel("Date", fontsize=10, color="#334155")
    ax.set_ylabel("Energy (kWh)", fontsize=10, color="#334155")
    ax.set_title("Daily Energy Trend", fontsize=12, fontweight="bold", color="#0f172a", pad=10)
    for label in ax.get_xticklabels():
        label.set_rotation(35)
        label.set_ha("right")
        label.set_fontsize(8)
        label.set_color("#334155")
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="y", alpha=0.22, linestyle="--")
    if values:
        ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.tight_layout()
    
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.getvalue()).decode()
    plt.close(fig)
    
    return f"data:image/png;base64,{image_base64}"


def demand_curve_chart(window_averages: list[float], window_minutes: int = 15) -> str:
    if not window_averages:
        return ""
    
    fig, ax = plt.subplots(figsize=(12, 5))
    x_values = list(range(1, len(window_averages) + 1))
    ax.plot(x_values, window_averages, marker="o", linewidth=2.2, color="#1d4ed8", markersize=4)
    
    max_idx = window_averages.index(max(window_averages))
    ax.axhline(y=window_averages[max_idx], color="#ef4444", linestyle="--", alpha=0.55, label=f"Peak: {max(window_averages):.2f} kW")
    
    _style_axes(ax)
    ax.set_xlabel(f"Demand Window ({window_minutes} min intervals)", fontsize=10)
    ax.set_ylabel("Average Power (kW)", fontsize=10)
    ax.set_title("Demand Curve Over Time", fontsize=12, fontweight="bold")
    ax.grid(True, alpha=0.22)
    ax.legend(frameon=False)
    fig.tight_layout()
    
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.getvalue()).decode()
    plt.close(fig)
    
    return f"data:image/png;base64,{image_base64}"


def power_factor_distribution_chart(pf_distribution: dict) -> str:
    if not pf_distribution:
        return ""
    
    labels = ["Good (≥0.95)", "Acceptable (0.85-0.95)", "Poor (<0.85)"]
    values = [
        pf_distribution.get("good", 0),
        pf_distribution.get("acceptable", 0),
        pf_distribution.get("poor", 0)
    ]
    
    if sum(values) == 0:
        return ""
    
    colors = ["#2E7D32", "#FFC107", "#D32F2F"]
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.pie(values, labels=labels, colors=colors, autopct="%1.1f%%", startangle=90)
    ax.set_title("Power Factor Distribution", fontsize=12, fontweight="bold")
    ax.axis("equal")
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.getvalue()).decode()
    plt.close(fig)
    
    return f"data:image/png;base64,{image_base64}"


def comparison_bar_chart(metrics: dict) -> str:
    labels = list(metrics.keys())
    values_a = [metrics[k].get("a", 0) for k in labels]
    values_b = [metrics[k].get("b", 0) for k in labels]
    
    x = np.arange(len(labels))
    width = 0.35
    
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar(x - width / 2, values_a, width, label="A", color="#1d4ed8")
    ax.bar(x + width / 2, values_b, width, label="B", color="#14b8a6")
    
    _style_axes(ax)
    ax.set_xlabel("Metrics", fontsize=10)
    ax.set_ylabel("Value", fontsize=10)
    ax.set_title("Comparison: Device A vs Device B", fontsize=12, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.22)
    fig.tight_layout()
    
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.getvalue()).decode()
    plt.close(fig)
    
    return f"data:image/png;base64,{image_base64}"


def device_share_donut(per_device: list[dict]) -> str:
    if not per_device:
        return ""

    labels = []
    values = []
    for d in per_device:
        kwh = float(d.get("total_kwh") or 0.0)
        if kwh <= 0:
            continue
        labels.append(d.get("device_name") or d.get("device_id") or "Device")
        values.append(kwh)

    if not values:
        return ""

    colors = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6"]
    fig, ax = plt.subplots(figsize=(7, 5))
    wedges, _, _ = ax.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        startangle=120,
        pctdistance=0.78,
        colors=colors[: len(values)],
        wedgeprops={"width": 0.35, "edgecolor": "white"},
        textprops={"fontsize": 9},
    )
    ax.set_title("Energy Share by Device", fontsize=12, fontweight="bold")
    fig.tight_layout()

    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=120, bbox_inches="tight")
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.getvalue()).decode()
    plt.close(fig)
    return f"data:image/png;base64,{image_base64}"


chart_generator = type('ChartGenerator', (), {
    'daily_energy_bar_chart': staticmethod(daily_energy_bar_chart),
    'demand_curve_chart': staticmethod(demand_curve_chart),
    'power_factor_distribution_chart': staticmethod(power_factor_distribution_chart),
    'comparison_bar_chart': staticmethod(comparison_bar_chart),
    'device_share_donut': staticmethod(device_share_donut),
})()
