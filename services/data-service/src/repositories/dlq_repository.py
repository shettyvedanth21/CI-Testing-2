"""Dead Letter Queue repository with pluggable durable backends."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Sequence

import pymysql
from sqlalchemy import create_engine, text

from src.config import settings
from src.models import DLQEntry
from src.utils import get_logger

logger = get_logger(__name__)


def _normalized_error_types(error_types: Sequence[str] | None) -> tuple[str, ...]:
    if not error_types:
        return ()
    normalized = [str(value).strip().lower() for value in error_types if str(value).strip()]
    return tuple(dict.fromkeys(normalized))


class DLQBackend(Protocol):
    """Protocol for DLQ backend implementations."""

    def send(
        self,
        entry: DLQEntry,
        *,
        initial_status: str = "pending",
        dead_reason: Optional[str] = None,
    ) -> bool:
        """Send entry to DLQ."""
        ...

    def get_operational_stats(self) -> Dict[str, Any]:
        """Return backend-specific DLQ stats."""
        ...

    def fetch_pending_retries(
        self,
        *,
        max_retry_count: int,
        grace_period: timedelta,
        limit: int,
        error_types: Sequence[str] | None = None,
    ) -> list[Dict[str, Any]]:
        """Fetch pending DLQ rows eligible for retry."""
        ...

    def mark_retry_reprocessed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
    ) -> None:
        """Mark a DLQ row as successfully reprocessed."""
        ...

    def mark_retry_failed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
        max_retry_count: int = 5,
    ) -> str:
        """Update retry metadata after a failed reprocessing attempt."""
        ...

    def mark_dead_without_retry_increment(
        self,
        *,
        message_id: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
    ) -> str:
        """Mark a DLQ row dead without changing its retry count."""
        ...

    def purge_expired(self, *, created_before: datetime, batch_size: int) -> int:
        """Purge DLQ rows past the configured retention boundary."""
        ...

    def reclassify_non_retryable_pending(
        self,
        *,
        retryable_error_types: Sequence[str],
        batch_size: int,
    ) -> int:
        """Mark non-retryable pending rows as dead to keep retry backlog bounded."""
        ...

    def close(self) -> None:
        """Close backend resources."""
        ...


class FileBasedDLQBackend:
    """File-based DLQ backend with rotation support."""

    def __init__(
        self,
        directory: str = "./dlq",
        max_file_size: int = 10 * 1024 * 1024,
        max_files: int = 10,
    ):
        self.directory = Path(directory)
        self.max_file_size = max_file_size
        self.max_files = max_files
        self._lock = threading.Lock()
        self._current_file: Optional[Path] = None
        self._file_handle: Optional[Any] = None
        self._entries_written = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        self._open_current_file()
        logger.info(
            "FileBasedDLQBackend initialized",
            directory=str(self.directory),
            max_file_size=self.max_file_size,
            max_files=self.max_files,
        )

    def _open_current_file(self) -> None:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._current_file = self.directory / f"dlq_{timestamp}.jsonl"
        self._file_handle = open(self._current_file, "a", encoding="utf-8")

    def _rotate_if_needed(self) -> None:
        if self._current_file is None:
            return
        if self._current_file.stat().st_size < self.max_file_size:
            return
        if self._file_handle:
            self._file_handle.close()
        self._open_current_file()
        self._cleanup_old_files()

    def _cleanup_old_files(self) -> None:
        dlq_files = sorted(
            self.directory.glob("dlq_*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if len(dlq_files) <= self.max_files:
            return
        for file_path in dlq_files[self.max_files :]:
            try:
                file_path.unlink()
            except OSError as exc:
                logger.error("Failed to remove old DLQ file", file=str(file_path), error=str(exc))

    def send(
        self,
        entry: DLQEntry,
        *,
        initial_status: str = "pending",
        dead_reason: Optional[str] = None,
    ) -> bool:
        with self._lock:
            try:
                self._rotate_if_needed()
                if self._file_handle is None:
                    self._open_current_file()
                payload = json.dumps(entry.model_dump(), default=str)
                self._file_handle.write(payload + "\n")
                self._file_handle.flush()
                self._entries_written += 1
                return True
            except Exception as exc:
                logger.error("Failed to write DLQ entry", error=str(exc), error_type=entry.error_type)
                return False

    def get_operational_stats(self) -> Dict[str, Any]:
        return {
            "backend": "file",
            "entries_written": self._entries_written,
            "active_file": str(self._current_file) if self._current_file else None,
        }

    def fetch_pending_retries(
        self,
        *,
        max_retry_count: int,
        grace_period: timedelta,
        limit: int,
        error_types: Sequence[str] | None = None,
    ) -> list[Dict[str, Any]]:
        return []

    def mark_retry_reprocessed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
    ) -> None:
        return None

    def mark_retry_failed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
        max_retry_count: int = 5,
    ) -> str:
        return "dead" if retry_count >= max_retry_count else "pending"

    def mark_dead_without_retry_increment(
        self,
        *,
        message_id: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
    ) -> str:
        return "dead"

    def purge_expired(self, *, created_before: datetime, batch_size: int) -> int:
        return 0

    def reclassify_non_retryable_pending(
        self,
        *,
        retryable_error_types: Sequence[str],
        batch_size: int,
    ) -> int:
        return 0

    def close(self) -> None:
        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception as exc:
                    logger.error("Error closing DLQ file handle", error=str(exc))


class MySQLDLQBackend:
    """MySQL-backed DLQ backend for durable storage."""

    TABLE_NAME = "dlq_messages"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries_written = 0
        self._write_failures = 0
        self._engine = create_engine(
            settings.mysql_sync_url,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=settings.db_pool_recycle,
            pool_timeout=settings.db_pool_timeout,
            future=True,
        )
        self._ensure_schema()
        logger.info("MySQLDLQBackend initialized", table=self.TABLE_NAME)

    def _ensure_schema(self) -> None:
        create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME(6) NOT NULL,
            error_type VARCHAR(128) NOT NULL,
            error_message TEXT NOT NULL,
            retry_count INT NOT NULL DEFAULT 0,
            original_payload JSON NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            last_retry_at DATETIME(6) NULL,
            dead_reason TEXT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        alter_statements = [
            f"ALTER TABLE {self.TABLE_NAME} ADD COLUMN last_retry_at DATETIME(6) NULL",
            f"ALTER TABLE {self.TABLE_NAME} ADD COLUMN dead_reason TEXT NULL",
        ]
        index_statements = [
            f"CREATE INDEX idx_dlq_messages_created_at ON {self.TABLE_NAME}(created_at)",
            f"CREATE INDEX idx_dlq_messages_error_type ON {self.TABLE_NAME}(error_type)",
            f"CREATE INDEX idx_dlq_messages_status_created ON {self.TABLE_NAME}(status, created_at)",
        ]
        with self._engine.begin() as conn:
            conn.exec_driver_sql(create_table_sql)
            for statement in alter_statements:
                try:
                    conn.exec_driver_sql(statement)
                except Exception:
                    # Column already exists.
                    pass
            for statement in index_statements:
                try:
                    conn.exec_driver_sql(statement)
                except Exception:
                    # Index already exists.
                    pass

    def send(
        self,
        entry: DLQEntry,
        *,
        initial_status: str = "pending",
        dead_reason: Optional[str] = None,
    ) -> bool:
        payload = entry.model_dump()
        payload_json = json.dumps(payload["original_payload"], default=str)
        timestamp = entry.timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        normalized_status = str(initial_status or "pending").strip().lower()
        if normalized_status not in {"pending", "dead", "reprocessed"}:
            normalized_status = "pending"
        with self._lock:
            try:
                with self._engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"""
                        INSERT INTO {self.TABLE_NAME}
                          (timestamp, error_type, error_message, retry_count, original_payload, status, last_retry_at, dead_reason)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, NULL, %s)
                        """,
                        (
                            timestamp,
                            entry.error_type,
                            entry.error_message,
                            entry.retry_count,
                            payload_json,
                            normalized_status,
                            dead_reason if normalized_status == "dead" else None,
                        ),
                    )
                self._entries_written += 1
                return True
            except Exception as exc:
                self._write_failures += 1
                logger.error("Failed to persist DLQ entry in MySQL", error=str(exc), error_type=entry.error_type)
                return False

    def fetch_pending_retries(
        self,
        *,
        max_retry_count: int,
        grace_period: timedelta,
        limit: int,
        error_types: Sequence[str] | None = None,
    ) -> list[Dict[str, Any]]:
        created_before = datetime.utcnow() - grace_period
        sql = f"""
            SELECT
                id,
                error_type,
                error_message,
                retry_count,
                original_payload,
                status,
                created_at,
                last_retry_at,
                dead_reason
            FROM {self.TABLE_NAME}
            WHERE status = 'pending'
              AND retry_count < :max_retry_count
              AND created_at < :created_before
        """
        params: dict[str, Any] = {
            "max_retry_count": max_retry_count,
            "created_before": created_before,
            "limit": limit,
        }
        if error_types:
            error_type_list = [str(value) for value in error_types if str(value).strip()]
            if not error_type_list:
                return []
            placeholders = ", ".join(f":error_type_{index}" for index in range(len(error_type_list)))
            sql += f" AND error_type IN ({placeholders})"
            for index, value in enumerate(error_type_list):
                params[f"error_type_{index}"] = value
        sql += " ORDER BY created_at ASC LIMIT :limit"
        with self._engine.connect() as conn:
            result = conn.execute(text(sql), params)
            return [dict(row) for row in result.mappings().all()]

    def mark_retry_reprocessed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
    ) -> None:
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET status = 'reprocessed',
                    retry_count = %s,
                    last_retry_at = %s,
                    dead_reason = NULL
                WHERE id = %s
                """,
                (
                    retry_count,
                    last_retry_at,
                    message_id,
                ),
            )

    def mark_retry_failed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
        max_retry_count: int = 5,
    ) -> str:
        status = "dead" if retry_count >= max_retry_count else "pending"
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET status = %s,
                    retry_count = %s,
                    last_retry_at = %s,
                    dead_reason = %s
                WHERE id = %s
                """,
                (
                    status,
                    retry_count,
                    last_retry_at,
                    dead_reason if status == "dead" else None,
                    message_id,
                ),
            )
        return status

    def mark_dead_without_retry_increment(
        self,
        *,
        message_id: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
    ) -> str:
        with self._engine.begin() as conn:
            conn.exec_driver_sql(
                f"""
                UPDATE {self.TABLE_NAME}
                SET status = 'dead',
                    last_retry_at = %s,
                    dead_reason = %s
                WHERE id = %s
                """,
                (
                    last_retry_at,
                    dead_reason,
                    message_id,
                ),
            )
        return "dead"

    def get_operational_stats(self) -> Dict[str, Any]:
        stats = {
            "backend": "mysql",
            "entries_written": self._entries_written,
            "write_failures": self._write_failures,
            "backlog_count": None,
            "pending_total_count": None,
            "pending_retryable_count": None,
            "pending_non_retryable_count": None,
            "oldest_pending_created_at": None,
            "oldest_pending_retryable_created_at": None,
            "retryable_error_types": list(DLQRepository.retryable_error_types()),
        }
        try:
            with self._engine.connect() as conn:
                pending_result = conn.execute(
                    text(
                        f"""
                        SELECT COUNT(*) AS pending_total_count, MIN(created_at) AS oldest_pending_created_at
                        FROM {self.TABLE_NAME}
                        WHERE status='pending'
                        """
                    )
                )
                pending_row = pending_result.mappings().one_or_none() or {}
                pending_total = int(pending_row.get("pending_total_count") or 0)
                stats["pending_total_count"] = pending_total
                stats["oldest_pending_created_at"] = pending_row.get("oldest_pending_created_at")

                retryable_types = DLQRepository.retryable_error_types()
                if retryable_types:
                    placeholders = ", ".join(f":error_type_{index}" for index in range(len(retryable_types)))
                    params = {f"error_type_{index}": value for index, value in enumerate(retryable_types)}
                    retryable_result = conn.execute(
                        text(
                            f"""
                            SELECT COUNT(*) AS pending_retryable_count, MIN(created_at) AS oldest_pending_retryable_created_at
                            FROM {self.TABLE_NAME}
                            WHERE status='pending'
                              AND LOWER(error_type) IN ({placeholders})
                            """
                        ),
                        params,
                    )
                    retryable_row = retryable_result.mappings().one_or_none() or {}
                    pending_retryable = int(retryable_row.get("pending_retryable_count") or 0)
                    stats["oldest_pending_retryable_created_at"] = retryable_row.get("oldest_pending_retryable_created_at")
                else:
                    pending_retryable = 0
                    stats["oldest_pending_retryable_created_at"] = None

                stats["pending_retryable_count"] = pending_retryable
                stats["pending_non_retryable_count"] = max(0, pending_total - pending_retryable)
                stats["backlog_count"] = pending_retryable
        except Exception as exc:
            logger.warning("Failed to query MySQL DLQ operational stats", error=str(exc))
        return stats

    def purge_expired(self, *, created_before: datetime, batch_size: int) -> int:
        cutoff = created_before
        if cutoff.tzinfo is not None:
            cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        limit = max(1, int(batch_size))
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    f"""
                    DELETE FROM {self.TABLE_NAME}
                    WHERE created_at < :created_before
                    LIMIT :limit
                    """
                ),
                {"created_before": cutoff, "limit": limit},
            )
        return int(result.rowcount or 0)

    def reclassify_non_retryable_pending(
        self,
        *,
        retryable_error_types: Sequence[str],
        batch_size: int,
    ) -> int:
        normalized_retryable = _normalized_error_types(retryable_error_types)
        limit = max(1, int(batch_size))
        with self._engine.begin() as conn:
            if normalized_retryable:
                placeholders = ", ".join(f":error_type_{index}" for index in range(len(normalized_retryable)))
                params: dict[str, Any] = {
                    "limit": limit,
                    "dead_reason": "non_retryable_pending_reclassified",
                }
                for index, value in enumerate(normalized_retryable):
                    params[f"error_type_{index}"] = value
                result = conn.execute(
                    text(
                        f"""
                        UPDATE {self.TABLE_NAME}
                        SET status = 'dead',
                            dead_reason = COALESCE(dead_reason, :dead_reason),
                            last_retry_at = COALESCE(last_retry_at, UTC_TIMESTAMP(6))
                        WHERE status = 'pending'
                          AND LOWER(error_type) NOT IN ({placeholders})
                        ORDER BY created_at ASC
                        LIMIT :limit
                        """
                    ),
                    params,
                )
            else:
                result = conn.execute(
                    text(
                        f"""
                        UPDATE {self.TABLE_NAME}
                        SET status = 'dead',
                            dead_reason = COALESCE(dead_reason, :dead_reason),
                            last_retry_at = COALESCE(last_retry_at, UTC_TIMESTAMP(6))
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        LIMIT :limit
                        """
                    ),
                    {
                        "dead_reason": "non_retryable_pending_reclassified",
                        "limit": limit,
                    },
                )
        return int(result.rowcount or 0)

    def close(self) -> None:
        self._engine.dispose()


class DLQRepository:
    """DLQ repository with pluggable backend selection."""

    def __init__(self, backend: Optional[DLQBackend] = None):
        if backend is not None:
            self.backend = backend
        else:
            if settings.dlq_backend == "mysql":
                self.backend = MySQLDLQBackend()
            else:
                self.backend = FileBasedDLQBackend(
                    directory=settings.dlq_directory,
                    max_file_size=settings.dlq_max_file_size,
                    max_files=settings.dlq_max_files,
                )
        logger.info("DLQRepository initialized", backend=type(self.backend).__name__)

    def send(
        self,
        original_payload: Dict[str, Any],
        error_type: str,
        error_message: str,
        retry_count: int = 0,
        *,
        initial_status: str | None = None,
        dead_reason: Optional[str] = None,
    ) -> bool:
        resolved_error_type = str(error_type).strip()
        normalized_error_type = resolved_error_type.lower()
        resolved_initial_status = initial_status
        resolved_dead_reason = dead_reason
        if resolved_initial_status is None:
            if normalized_error_type in self.retryable_error_types():
                resolved_initial_status = "pending"
            else:
                resolved_initial_status = "dead"
                if not resolved_dead_reason:
                    resolved_dead_reason = error_message
        entry = DLQEntry(
            original_payload=original_payload,
            error_type=resolved_error_type,
            error_message=error_message,
            retry_count=retry_count,
        )
        success = self.backend.send(
            entry,
            initial_status=resolved_initial_status,
            dead_reason=resolved_dead_reason,
        )
        if success:
            logger.info(
                "Message sent to DLQ",
                error_type=resolved_error_type,
                device_id=original_payload.get("device_id", "unknown"),
            )
        else:
            logger.error(
                "Failed to send message to DLQ",
                error_type=resolved_error_type,
                device_id=original_payload.get("device_id", "unknown"),
            )
        return success

    def get_operational_stats(self) -> Dict[str, Any]:
        return self.backend.get_operational_stats()

    def fetch_pending_retries(
        self,
        *,
        max_retry_count: int,
        grace_period: timedelta,
        limit: int,
        error_types: Sequence[str] | None = None,
    ) -> list[Dict[str, Any]]:
        return self.backend.fetch_pending_retries(
            max_retry_count=max_retry_count,
            grace_period=grace_period,
            limit=limit,
            error_types=error_types,
        )

    def mark_retry_reprocessed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
    ) -> None:
        self.backend.mark_retry_reprocessed(
            message_id=message_id,
            retry_count=retry_count,
            last_retry_at=last_retry_at,
        )

    def mark_retry_failed(
        self,
        *,
        message_id: int,
        retry_count: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
        max_retry_count: int = 5,
    ) -> str:
        return self.backend.mark_retry_failed(
            message_id=message_id,
            retry_count=retry_count,
            last_retry_at=last_retry_at,
            dead_reason=dead_reason,
            max_retry_count=max_retry_count,
        )

    def mark_dead_without_retry_increment(
        self,
        *,
        message_id: int,
        last_retry_at: datetime,
        dead_reason: Optional[str] = None,
    ) -> str:
        return self.backend.mark_dead_without_retry_increment(
            message_id=message_id,
            last_retry_at=last_retry_at,
            dead_reason=dead_reason,
        )

    def purge_expired(self, *, created_before: datetime, batch_size: int) -> int:
        return self.backend.purge_expired(
            created_before=created_before,
            batch_size=batch_size,
        )

    def reclassify_non_retryable_pending(self, *, batch_size: int) -> int:
        return self.backend.reclassify_non_retryable_pending(
            retryable_error_types=self.retryable_error_types(),
            batch_size=batch_size,
        )

    @staticmethod
    def retryable_error_types() -> tuple[str, ...]:
        return _normalized_error_types(settings.dlq_retryable_error_types)

    def close(self) -> None:
        self.backend.close()
