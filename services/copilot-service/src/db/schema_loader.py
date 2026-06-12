import json
from typing import Any

from sqlalchemy import text

from src.database import get_readonly_db_session


SCHEMA_CONTEXT: str = ""
SCHEMA_MANIFEST: dict[str, Any] = {}


TABLE_DESCRIPTIONS = {
    "devices": "Onboarded machines and metadata.",
    "device_shifts": "Shift windows per machine.",
    "rules": "Rule definitions for alerts.",
    "alerts": "Alert records for triggered rule events.",
    "activity_events": "Activity/event stream for notifications.",
    "idle_running_log": "Per-period idle duration, energy and cost.",
    "tariff_config": "Current tariff settings for cost calculations.",
    "notification_channels": "Configured notification recipients.",
    "energy_reports": "Energy/comparison report job records.",
    "waste_analysis_jobs": "Waste analysis job records.",
    "waste_device_summary": "Per-device waste metrics output.",
    "device_performance_trends": "Materialized health/uptime trends.",
    "device_properties": "Discovered telemetry property metadata.",
}


def get_schema_context() -> str:
    return SCHEMA_CONTEXT


def get_schema_manifest() -> dict[str, Any]:
    return SCHEMA_MANIFEST


async def load_schema() -> tuple[str, dict[str, Any]]:
    global SCHEMA_CONTEXT, SCHEMA_MANIFEST

    manifest: dict[str, Any] = {"tables": {}}
    lines: list[str] = []

    async with get_readonly_db_session() as db:
        tables_result = await db.execute(text("SHOW TABLES"))
        table_names = [row[0] for row in tables_result.fetchall()]

        for table in table_names:
            columns_result = await db.execute(text(f"DESCRIBE `{table}`"))
            columns = [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": row[2],
                    "key": row[3],
                    "default": row[4],
                }
                for row in columns_result.fetchall()
            ]
            manifest["tables"][table] = {
                "description": TABLE_DESCRIPTIONS.get(table, ""),
                "columns": columns,
            }

            col_txt = ", ".join(f"{c['name']} ({c['type']})" for c in columns)
            desc = TABLE_DESCRIPTIONS.get(table, "")
            lines.append(f"TABLE {table}: {col_txt}")
            if desc:
                lines.append(f"  -> {desc}")

    SCHEMA_CONTEXT = "\n".join(lines)
    SCHEMA_MANIFEST = manifest

    try:
        with open("/tmp/copilot_schema_manifest.json", "w", encoding="utf-8") as fp:
            json.dump(manifest, fp, indent=2, default=str)
    except Exception:
        # Non-fatal artifact write.
        pass

    return SCHEMA_CONTEXT, SCHEMA_MANIFEST
