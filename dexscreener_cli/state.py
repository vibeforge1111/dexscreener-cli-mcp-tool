from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal
from uuid import uuid4

from .config import DEFAULT_CHAINS, ScanFilters

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TaskStatus = Literal["todo", "running", "done", "blocked"]
_VALID_TASK_STATUSES: frozenset[str] = frozenset({"todo", "running", "done", "blocked"})
_MAX_IMPORTED_RUNS = 5_000


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class ScanPreset:
    name: str
    chains: tuple[str, ...]
    limit: int
    min_liquidity_usd: float
    min_volume_h24_usd: float
    min_txns_h1: int
    min_price_change_h1: float
    created_at: str
    updated_at: str

    @classmethod
    def from_filters(cls, name: str, filters: ScanFilters) -> ScanPreset:
        now = utc_now_iso()
        return cls(
            name=name,
            chains=filters.chains,
            limit=filters.limit,
            min_liquidity_usd=filters.min_liquidity_usd,
            min_volume_h24_usd=filters.min_volume_h24_usd,
            min_txns_h1=filters.min_txns_h1,
            min_price_change_h1=filters.min_price_change_h1,
            created_at=now,
            updated_at=now,
        )

    def to_filters(self) -> ScanFilters:
        return ScanFilters(
            chains=self.chains,
            limit=self.limit,
            min_liquidity_usd=self.min_liquidity_usd,
            min_volume_h24_usd=self.min_volume_h24_usd,
            min_txns_h1=self.min_txns_h1,
            min_price_change_h1=self.min_price_change_h1,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScanPreset:
        return cls(
            name=str(payload["name"]),
            chains=tuple(payload.get("chains", DEFAULT_CHAINS)),
            limit=int(payload.get("limit", 20)),
            min_liquidity_usd=float(payload.get("min_liquidity_usd", 35_000.0)),
            min_volume_h24_usd=float(payload.get("min_volume_h24_usd", 90_000.0)),
            min_txns_h1=int(payload.get("min_txns_h1", 80)),
            min_price_change_h1=float(payload.get("min_price_change_h1", 0.0)),
            created_at=str(payload.get("created_at", utc_now_iso())),
            updated_at=str(payload.get("updated_at", utc_now_iso())),
        )

    def to_dict(self) -> dict[str, Any]:
        obj = asdict(self)
        obj["chains"] = list(self.chains)
        return obj


@dataclass(slots=True)
class ScanTask:
    id: str
    name: str
    preset: str | None
    filters: dict[str, Any] | None
    interval_seconds: int | None
    alerts: dict[str, Any] | None
    status: TaskStatus
    notes: str
    created_at: str
    updated_at: str
    last_run_at: str | None
    last_alert_at: str | None

    @classmethod
    def create(
        cls,
        *,
        name: str,
        preset: str | None = None,
        filters: dict[str, Any] | None = None,
        interval_seconds: int | None = None,
        alerts: dict[str, Any] | None = None,
        status: TaskStatus = "todo",
        notes: str = "",
    ) -> ScanTask:
        now = utc_now_iso()
        return cls(
            id=uuid4().hex[:10],
            name=name,
            preset=preset,
            filters=filters,
            interval_seconds=interval_seconds,
            alerts=alerts,
            status=status,
            notes=notes,
            created_at=now,
            updated_at=now,
            last_run_at=None,
            last_alert_at=None,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScanTask:
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            preset=payload.get("preset"),
            filters=payload.get("filters"),
            interval_seconds=payload.get("interval_seconds"),
            alerts=payload.get("alerts"),
            status=str(payload.get("status", "todo")),  # type: ignore[assignment]
            notes=str(payload.get("notes", "")),
            created_at=str(payload.get("created_at", utc_now_iso())),
            updated_at=str(payload.get("updated_at", utc_now_iso())),
            last_run_at=payload.get("last_run_at"),
            last_alert_at=payload.get("last_alert_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskRunRecord:
    id: str
    task_id: str
    task_name: str
    mode: str
    started_at: str
    finished_at: str
    duration_ms: int
    status: str
    result_count: int
    top_chain: str | None
    top_token: str | None
    top_score: float | None
    alert_sent: bool
    alert_reason: str
    error: str | None

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        task_name: str,
        mode: str,
        started_at: str,
        finished_at: str,
        duration_ms: int,
        status: str,
        result_count: int,
        top_chain: str | None = None,
        top_token: str | None = None,
        top_score: float | None = None,
        alert_sent: bool = False,
        alert_reason: str = "n/a",
        error: str | None = None,
    ) -> TaskRunRecord:
        return cls(
            id=uuid4().hex[:12],
            task_id=task_id,
            task_name=task_name,
            mode=mode,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            status=status,
            result_count=result_count,
            top_chain=top_chain,
            top_token=top_token,
            top_score=top_score,
            alert_sent=alert_sent,
            alert_reason=alert_reason,
            error=error,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskRunRecord:
        return cls(
            id=str(payload["id"]),
            task_id=str(payload.get("task_id", "")),
            task_name=str(payload.get("task_name", "")),
            mode=str(payload.get("mode", "manual")),
            started_at=str(payload.get("started_at", utc_now_iso())),
            finished_at=str(payload.get("finished_at", utc_now_iso())),
            duration_ms=int(payload.get("duration_ms", 0)),
            status=str(payload.get("status", "ok")),
            result_count=int(payload.get("result_count", 0)),
            top_chain=payload.get("top_chain"),
            top_token=payload.get("top_token"),
            top_score=payload.get("top_score"),
            alert_sent=bool(payload.get("alert_sent", False)),
            alert_reason=str(payload.get("alert_reason", "n/a")),
            error=payload.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (Path.home() / ".dexscreener-cli")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.base_dir / ".state.lock"
        self.presets_file = self.base_dir / "presets.json"
        self.tasks_file = self.base_dir / "tasks.json"
        self.runs_file = self.base_dir / "runs.json"
        self._state_lock = threading.RLock()
        self._lock_depth = 0
        self._lock_handle: Any | None = None

    def _acquire_file_lock(self) -> None:
        handle = self.lock_file.open("a+", encoding="utf-8")
        handle.seek(0)
        handle.write("0")
        handle.flush()
        handle.seek(0)
        if "msvcrt" in globals():
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        self._lock_handle = handle

    def _release_file_lock(self) -> None:
        handle = self._lock_handle
        if handle is None:
            return
        handle.seek(0)
        if "msvcrt" in globals():
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._lock_handle = None

    def _lock_state(self) -> None:
        self._state_lock.acquire()
        if self._lock_depth == 0:
            self._acquire_file_lock()
        self._lock_depth += 1

    def _unlock_state(self) -> None:
        self._lock_depth -= 1
        if self._lock_depth == 0:
            self._release_file_lock()
        self._state_lock.release()

    def _with_state_lock(self) -> _StateLock:
        return _StateLock(self)

    def _load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _save_json(self, path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(path)

    # Presets
    def list_presets(self) -> list[ScanPreset]:
        with self._with_state_lock():
            data = self._load_json(self.presets_file)
            rows = [ScanPreset.from_dict(p) for p in data.get("presets", [])]
            rows.sort(key=lambda p: p.name.lower())
            return rows

    def get_preset(self, name: str) -> ScanPreset | None:
        with self._with_state_lock():
            wanted = name.strip().lower()
            for preset in self.list_presets():
                if preset.name.lower() == wanted:
                    return preset
            return None

    def save_preset(self, preset: ScanPreset) -> ScanPreset:
        with self._with_state_lock():
            rows = self.list_presets()
            existing = self.get_preset(preset.name)
            if existing:
                preset.created_at = existing.created_at
            preset.updated_at = utc_now_iso()
            new_rows = [p for p in rows if p.name.lower() != preset.name.lower()]
            new_rows.append(preset)
            new_rows.sort(key=lambda p: p.name.lower())
            self._save_json(self.presets_file, {"presets": [p.to_dict() for p in new_rows]})
            return preset

    def delete_preset(self, name: str) -> bool:
        with self._with_state_lock():
            rows = self.list_presets()
            new_rows = [p for p in rows if p.name.lower() != name.strip().lower()]
            if len(new_rows) == len(rows):
                return False
            self._save_json(self.presets_file, {"presets": [p.to_dict() for p in new_rows]})
            return True

    # Tasks
    def list_tasks(self, status: TaskStatus | None = None) -> list[ScanTask]:
        with self._with_state_lock():
            data = self._load_json(self.tasks_file)
            rows = [ScanTask.from_dict(t) for t in data.get("tasks", [])]
            if status:
                rows = [t for t in rows if t.status == status]
            rows.sort(key=lambda t: (t.status, t.updated_at), reverse=False)
            return rows

    def get_task(self, name_or_id: str) -> ScanTask | None:
        with self._with_state_lock():
            key = name_or_id.strip().lower()
            for task in self.list_tasks():
                if task.id.lower() == key or task.name.lower() == key:
                    return task
            return None

    def create_task(
        self,
        *,
        name: str,
        preset: str | None = None,
        filters: dict[str, Any] | None = None,
        interval_seconds: int | None = None,
        alerts: dict[str, Any] | None = None,
        notes: str = "",
    ) -> ScanTask:
        with self._with_state_lock():
            rows = self.list_tasks()
            if any(t.name.lower() == name.lower() for t in rows):
                raise ValueError(f"Task '{name}' already exists")
            task = ScanTask.create(
                name=name,
                preset=preset,
                filters=filters,
                interval_seconds=interval_seconds,
                alerts=alerts,
                notes=notes,
            )
            rows.append(task)
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in rows]})
            return task

    def update_task_status(self, name_or_id: str, status: TaskStatus) -> ScanTask:
        with self._with_state_lock():
            rows = self.list_tasks()
            updated: ScanTask | None = None
            for task in rows:
                if task.id.lower() == name_or_id.lower() or task.name.lower() == name_or_id.lower():
                    task.status = status
                    task.updated_at = utc_now_iso()
                    updated = task
                    break
            if not updated:
                raise ValueError(f"Task '{name_or_id}' not found")
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in rows]})
            return updated

    def touch_task_run(self, name_or_id: str) -> ScanTask:
        with self._with_state_lock():
            rows = self.list_tasks()
            updated: ScanTask | None = None
            for task in rows:
                if task.id.lower() == name_or_id.lower() or task.name.lower() == name_or_id.lower():
                    now = utc_now_iso()
                    task.last_run_at = now
                    task.updated_at = now
                    updated = task
                    break
            if not updated:
                raise ValueError(f"Task '{name_or_id}' not found")
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in rows]})
            return updated

    def touch_task_alert(self, name_or_id: str) -> ScanTask:
        with self._with_state_lock():
            rows = self.list_tasks()
            updated: ScanTask | None = None
            for task in rows:
                if task.id.lower() == name_or_id.lower() or task.name.lower() == name_or_id.lower():
                    now = utc_now_iso()
                    task.last_alert_at = now
                    task.updated_at = now
                    updated = task
                    break
            if not updated:
                raise ValueError(f"Task '{name_or_id}' not found")
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in rows]})
            return updated

    def update_task(
        self,
        name_or_id: str,
        *,
        preset: str | None = None,
        filters: dict[str, Any] | None = None,
        interval_seconds: int | None = None,
        alerts: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> ScanTask:
        with self._with_state_lock():
            rows = self.list_tasks()
            updated: ScanTask | None = None
            for task in rows:
                if task.id.lower() == name_or_id.lower() or task.name.lower() == name_or_id.lower():
                    task.preset = preset
                    task.filters = filters
                    task.interval_seconds = interval_seconds
                    task.alerts = alerts
                    if notes is not None:
                        task.notes = notes
                    task.updated_at = utc_now_iso()
                    updated = task
                    break
            if not updated:
                raise ValueError(f"Task '{name_or_id}' not found")
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in rows]})
            return updated

    def delete_task(self, name_or_id: str) -> bool:
        with self._with_state_lock():
            rows = self.list_tasks()
            key = name_or_id.strip().lower()
            new_rows = [t for t in rows if t.id.lower() != key and t.name.lower() != key]
            if len(new_rows) == len(rows):
                return False
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in new_rows]})
            return True

    # Runs
    def list_runs(self, task: str | None = None, limit: int = 200) -> list[TaskRunRecord]:
        with self._with_state_lock():
            data = self._load_json(self.runs_file)
            rows = [TaskRunRecord.from_dict(r) for r in data.get("runs", [])]
            if task:
                key = task.strip().lower()
                rows = [r for r in rows if r.task_id.lower() == key or r.task_name.lower() == key]
            rows.sort(key=lambda r: r.finished_at, reverse=True)
            return rows[:limit]

    def append_run(self, run: TaskRunRecord) -> TaskRunRecord:
        with self._with_state_lock():
            rows = self.list_runs(limit=10_000)
            rows.append(run)
            rows.sort(key=lambda r: r.finished_at, reverse=False)
            # Keep file bounded for local usage.
            rows = rows[-5000:]
            self._save_json(self.runs_file, {"runs": [r.to_dict() for r in rows]})
            return run

    # State snapshot
    _REDACTED_ALERT_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "webhook_url",
            "discord_webhook_url",
            "telegram_bot_token",
            "telegram_chat_id",
        }
    )

    @classmethod
    def _redact_task(cls, task_dict: dict[str, Any]) -> dict[str, Any]:
        """Strip sensitive alert credentials from a task dict for safe export."""
        alerts = task_dict.get("alerts")
        if not alerts or not isinstance(alerts, dict):
            return task_dict
        cleaned = {k: v for k, v in alerts.items() if k not in cls._REDACTED_ALERT_KEYS}
        # Leave a marker so the importer knows credentials were stripped.
        if any(k in alerts for k in cls._REDACTED_ALERT_KEYS):
            cleaned["_redacted"] = True
        return {**task_dict, "alerts": cleaned}

    def export_bundle(self) -> dict[str, Any]:
        with self._with_state_lock():
            return {
                "version": 1,
                "exported_at": utc_now_iso(),
                "presets": [p.to_dict() for p in self.list_presets()],
                "tasks": [self._redact_task(t.to_dict()) for t in self.list_tasks()],
                "runs": [r.to_dict() for r in self.list_runs(limit=50_000)],
            }

    def import_bundle(self, bundle: dict[str, Any], mode: Literal["merge", "replace"] = "merge") -> dict[str, int]:
        with self._with_state_lock():
            if not isinstance(bundle, dict):
                raise ValueError("Bundle must be a JSON object")

            presets_raw = bundle.get("presets", [])
            tasks_raw = bundle.get("tasks", [])
            runs_raw = bundle.get("runs", [])
            if not isinstance(presets_raw, list) or not isinstance(tasks_raw, list) or not isinstance(runs_raw, list):
                raise ValueError("Bundle presets/tasks/runs must be arrays")
            if len(runs_raw) > _MAX_IMPORTED_RUNS:
                raise ValueError(f"Bundle exceeds max {_MAX_IMPORTED_RUNS} runs")
            if not all(isinstance(item, dict) for item in presets_raw):
                raise ValueError("Bundle presets must contain only objects")
            if not all(isinstance(item, dict) for item in tasks_raw):
                raise ValueError("Bundle tasks must contain only objects")
            if not all(isinstance(item, dict) for item in runs_raw):
                raise ValueError("Bundle runs must contain only objects")
            if any(str(item.get("status", "todo")) not in _VALID_TASK_STATUSES for item in tasks_raw):
                raise ValueError("Bundle contains invalid task status")

            presets_in = [ScanPreset.from_dict(p) for p in presets_raw]
            tasks_in = [ScanTask.from_dict(t) for t in tasks_raw]
            runs_in = [TaskRunRecord.from_dict(r) for r in runs_raw]

            if mode == "replace":
                self._save_json(self.presets_file, {"presets": [p.to_dict() for p in presets_in]})
                self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in tasks_in]})
                self._save_json(self.runs_file, {"runs": [r.to_dict() for r in runs_in]})
                return {"presets": len(presets_in), "tasks": len(tasks_in), "runs": len(runs_in)}

            # merge mode
            preset_map = {p.name.lower(): p for p in self.list_presets()}
            for p in presets_in:
                preset_map[p.name.lower()] = p
            merged_presets = sorted(preset_map.values(), key=lambda p: p.name.lower())
            self._save_json(self.presets_file, {"presets": [p.to_dict() for p in merged_presets]})

            task_map = {t.name.lower(): t for t in self.list_tasks()}
            for t in tasks_in:
                task_map[t.name.lower()] = t
            merged_tasks = list(task_map.values())
            self._save_json(self.tasks_file, {"tasks": [t.to_dict() for t in merged_tasks]})

            run_map = {r.id: r for r in self.list_runs(limit=50_000)}
            for r in runs_in:
                run_map[r.id] = r
            merged_runs = sorted(run_map.values(), key=lambda r: r.finished_at, reverse=False)[-5000:]
            self._save_json(self.runs_file, {"runs": [r.to_dict() for r in merged_runs]})
            return {"presets": len(merged_presets), "tasks": len(merged_tasks), "runs": len(merged_runs)}


class _StateLock:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    def __enter__(self) -> None:
        self._store._lock_state()
        return None

    def __exit__(self, *_: Any) -> None:
        self._store._unlock_state()
        return None
