import asyncio
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import text

from src.config import settings
from src.database import get_readonly_db_session
from src.db.schema_loader import get_schema_manifest
from src.utils.sql_guard import SQLGuard


logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    error: str | None = None
    reason: str | None = None


class QueryEngine:
    _TABLE_REF_PATTERN = re.compile(
        r"\b(?:FROM|JOIN)\s+([`\"\w.]+)(?:\s+(?:AS\s+)?([`\"\w]+))?",
        re.IGNORECASE,
    )
    _CLAUSE_PATTERN = re.compile(r"\b(GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b", re.IGNORECASE)

    @staticmethod
    def _strip_literals_and_comments(sql: str) -> str:
        return SQLGuard._strip_comments(sql)

    @staticmethod
    def validate_sql(sql: str) -> tuple[bool, str]:
        return SQLGuard().validate(sql)

    @staticmethod
    @lru_cache(maxsize=1)
    def _tenant_scoped_tables() -> frozenset[str]:
        manifest = get_schema_manifest() or {}
        tables = manifest.get("tables", {}) if isinstance(manifest, dict) else {}
        scoped: set[str] = set()
        for table_name, table_meta in tables.items():
            columns = table_meta.get("columns", []) if isinstance(table_meta, dict) else []
            for column in columns:
                if str(column.get("name", "")).strip().lower() == "tenant_id":
                    scoped.add(str(table_name).strip().lower())
                    break
        return frozenset(scoped)

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        cleaned = identifier.strip().strip("`\"")
        if "." in cleaned:
            cleaned = cleaned.split(".")[-1]
        return cleaned

    @classmethod
    def _extract_table_refs(cls, sql: str) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = []
        for match in cls._TABLE_REF_PATTERN.finditer(sql):
            table = cls._normalize_identifier(match.group(1))
            if not table or table.startswith("("):
                continue
            alias = match.group(2)
            alias_name = cls._normalize_identifier(alias) if alias else table
            refs.append((table, alias_name))
        return refs

    @classmethod
    def _tenant_predicate(cls, alias_name: str) -> str:
        return f"({alias_name}.tenant_id = :tenant_id)"

    @classmethod
    def _inject_tenant_filter(cls, sql: str) -> str:
        cleaned_sql = (sql or "").strip().rstrip(";")
        clause_match = cls._CLAUSE_PATTERN.search(cleaned_sql)
        if clause_match:
            base_sql = cleaned_sql[: clause_match.start()].rstrip()
            suffix = cleaned_sql[clause_match.start() :].lstrip()
        else:
            base_sql = cleaned_sql
            suffix = ""

        refs = cls._extract_table_refs(base_sql)
        tenant_tables = cls._tenant_scoped_tables()
        predicates: list[str] = []
        seen_aliases: set[str] = set()

        for table_name, alias_name in refs:
            if table_name.lower() not in tenant_tables:
                continue
            alias_key = alias_name.lower()
            if alias_key in seen_aliases:
                continue
            seen_aliases.add(alias_key)
            predicates.append(cls._tenant_predicate(alias_name))

        if not predicates:
            return f"{base_sql} {suffix}".strip() if suffix else base_sql

        tenant_clause = " AND ".join(predicates)
        if re.search(r"\bWHERE\b", base_sql, flags=re.IGNORECASE):
            filtered = f"{base_sql} AND {tenant_clause}"
        else:
            filtered = f"{base_sql} WHERE {tenant_clause}"

        return f"{filtered} {suffix}".strip() if suffix else filtered

    async def execute_query(self, sql: str, tenant_id: str) -> QueryResult:
        guard = SQLGuard()
        valid, reason = guard.validate(sql)
        if not valid:
            logger.warning("copilot_sql_blocked", extra={"reason": reason, "sql_preview": (sql or "")[:100]})
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_BLOCKED", reason=reason)

        try:
            safe_sql = self._inject_tenant_filter(sql)
        except ValueError as exc:
            logger.warning("copilot_sql_blocked", extra={"reason": str(exc), "sql_preview": (sql or "")[:100]})
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_BLOCKED", reason=str(exc))

        async def _run() -> QueryResult:
            async with get_readonly_db_session() as db:
                params = {"tenant_id": tenant_id} if ":tenant_id" in safe_sql else {}
                result = await db.execute(text(safe_sql), params)
                rows = result.fetchmany(settings.max_query_rows)
                columns = list(result.keys())
                return QueryResult(
                    columns=columns,
                    rows=[list(r) for r in rows],
                    row_count=len(rows),
                )

        try:
            return await asyncio.wait_for(_run(), timeout=settings.query_timeout_sec)
        except asyncio.TimeoutError:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_TIMEOUT", reason="Query timed out")
        except Exception as exc:
            return QueryResult(columns=[], rows=[], row_count=0, error="QUERY_FAILED", reason=str(exc))
