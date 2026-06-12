"""SQL safety guardrails for LLM-generated queries."""

from __future__ import annotations

import re


class SQLGuard:
    ALLOWED_STATEMENTS = ["SELECT"]
    BLOCKED_KEYWORDS = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "TRUNCATE",
        "ALTER",
        "CREATE",
        "GRANT",
        "REVOKE",
        "EXEC",
        "EXECUTE",
        "UNION",
        "INTO OUTFILE",
        "LOAD_FILE",
        "BENCHMARK",
        "SLEEP",
        "INFORMATION_SCHEMA",
    ]
    MAX_QUERY_LENGTH = 2000

    @staticmethod
    def _blocked_keyword_pattern(keyword: str) -> re.Pattern[str]:
        escaped = re.escape(keyword).replace(r"\ ", r"\s+")
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)

    @staticmethod
    def _strip_comments(sql: str) -> str:
        cleaned = re.sub(r"/\*.*?\*/", " ", sql or "", flags=re.DOTALL)
        cleaned = re.sub(r"(--[^\n]*|#[^\n]*)", " ", cleaned)
        return cleaned.strip()

    def validate(self, sql: str) -> tuple[bool, str]:
        raw = self._strip_comments(sql)
        upper = raw.upper()

        if not raw:
            return False, "Empty query"
        if len(raw) > self.MAX_QUERY_LENGTH:
            return False, "Query too long"
        if ";" in raw:
            statements = [part.strip() for part in raw.split(";") if part.strip()]
            if len(statements) > 1:
                return False, "Semicolons are not allowed (Multiple statements are not allowed)"
            return False, "Semicolons are not allowed"
        if not any(upper.startswith(stmt) for stmt in self.ALLOWED_STATEMENTS):
            return False, "Only SELECT statements are allowed"

        for keyword in self.BLOCKED_KEYWORDS:
            if self._blocked_keyword_pattern(keyword).search(upper):
                return False, f"Blocked keyword: {keyword}"

        if re.search(r"\bFROM\s+(INFORMATION_SCHEMA|MYSQL|PERFORMANCE_SCHEMA|SYS)\b", upper):
            return False, "System tables are not allowed"
        if re.search(r"\(\s*SELECT\b.*\b(INFORMATION_SCHEMA|MYSQL|PERFORMANCE_SCHEMA|SYS)\b", upper, flags=re.DOTALL):
            return False, "System table subqueries are not allowed"

        return True, "ok"

    def inject_tenant_filter(self, sql: str, tenant_id: str) -> str:
        cleaned_sql = (sql or "").strip().rstrip(";")
        if not re.search(r"\bFROM\b", cleaned_sql, flags=re.IGNORECASE):
            raise ValueError("Query must contain FROM clause for tenant isolation")

        clause_match = re.search(
            r"\b(GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING)\b",
            cleaned_sql,
            flags=re.IGNORECASE,
        )
        clause_start = clause_match.start() if clause_match else len(cleaned_sql)
        base_sql = cleaned_sql[:clause_start].rstrip()
        suffix = cleaned_sql[clause_start:]

        if re.search(r"\bWHERE\b", base_sql, flags=re.IGNORECASE):
            return f"{base_sql} AND tenant_id = :tenant_id{suffix}"
        return f"{base_sql} WHERE tenant_id = :tenant_id{suffix}"
