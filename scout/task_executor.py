from __future__ import annotations

import errno
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from threading import RLock
from typing import Any, Generator

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scout.aivio import AivioIntegration, CDP_ENDPOINT
from scout.browser_controller import BrowserController
from scout.v2_models import (
    LeadTaskPayload,
    SearchLeadsPayload,
    Task,
    TaskResult,
    TaskStatus,
    TaskType,
    V2Lead,
)

logger = logging.getLogger("scout.v2.tasks")
_BROWSER_TASK_LOCK = RLock()
_TASK_SECRET_PATTERNS = (
    re.compile(r"(?i)(https?://)[^/\s:@]+:[^@\s/]+@"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
    re.compile(
        r"""(?i)(?:access[_ -]?token|refresh[_ -]?token|csrf(?:[_ -]?token)?|"""
        r"""authorization|cookie|password|secret|api[_ -]?key)\s*[:=]\s*"""
        r"""["']?[^\s,;"'}&#]+"""
    ),
)


def _safe_error_message(error: Exception) -> str:
    message = f"{type(error).__name__}: {error}"
    for pattern in _TASK_SECRET_PATTERNS:
        message = pattern.sub("[REDACTED]", message)
    return message


@contextmanager
def _browser_execution_lock(endpoint: str) -> Generator[None, None, None]:
    lock_name = sha256(endpoint.encode("utf-8")).hexdigest()[:16]
    lock_dir = tempfile.gettempdir()
    if hasattr(os, "getuid"):
        lock_dir = os.path.join(lock_dir, f"auren-scout-{os.getuid()}")
        os.makedirs(lock_dir, mode=0o700, exist_ok=True)
        directory_stat = os.stat(lock_dir)
        if directory_stat.st_uid != os.getuid() or directory_stat.st_mode & 0o077:
            raise PermissionError(f"Diretório de lock não é privado: {lock_dir}")
    lock_path = os.path.join(lock_dir, f"auren-scout-{lock_name}.lock")
    with _BROWSER_TASK_LOCK, open(lock_path, "a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EDEADLK):
                        raise
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class TaskExecutor:
    def __init__(
        self,
        integration: AivioIntegration | None = None,
        browser_controller: BrowserController | None = None,
    ) -> None:
        timeout = int(os.getenv("SCOUT_AIVIO_GENERATION_TIMEOUT_MS", "120000"))
        if integration is None:
            browser_controller = browser_controller or BrowserController(
                cdp_endpoint=os.getenv("SCOUT_CDP_ENDPOINT", CDP_ENDPOINT)
            )
            integration = AivioIntegration(browser_controller, generation_timeout_ms=timeout)
        self.integration = integration
        self.browser_controller = browser_controller or getattr(integration, "browser_controller", None)
        self._browser_endpoint = (
            getattr(self.browser_controller, "cdp_endpoint", None) or CDP_ENDPOINT
        )
        self._task_results: dict[str, TaskResult] = {}
        self._task_results_lock = RLock()

    def execute(self, value: Task | dict[str, Any]) -> TaskResult:
        task = value if isinstance(value, Task) else Task.from_dict(value)
        queued_result = TaskResult(
            task_id=task.task_id,
            type=task.type,
            status=TaskStatus.QUEUED,
            started_at="",
            finished_at="",
        )
        with self._task_results_lock:
            if task.task_id in self._task_results:
                raise ValueError(f"task_id já foi executado: {task.task_id}")
            self._task_results[task.task_id] = queued_result
        try:
            with _browser_execution_lock(self._browser_endpoint):
                with self._task_results_lock:
                    queued_result.status = TaskStatus.RUNNING
                    queued_result.started_at = datetime.now(timezone.utc).isoformat()
                result = self._execute_task(task, queued_result)
                with self._task_results_lock:
                    result.finished_at = datetime.now(timezone.utc).isoformat()
                return result
        except Exception as error:
            message = _safe_error_message(error)
            logger.error("Falha ao iniciar tarefa V2 task_id=%s error=%s", task.task_id, message)
            with self._task_results_lock:
                queued_result.status = TaskStatus.FAILED
                queued_result.errors.append(message)
                queued_result.error = {"code": "task_start_failed", "message": message}
                queued_result.finished_at = datetime.now(timezone.utc).isoformat()
            return queued_result

    def get_task_result(self, task_id: str) -> dict[str, Any] | None:
        with self._task_results_lock:
            result = self._task_results.get(task_id)
            if result is None:
                return None
            if result.finished_at:
                return result.to_dict()
            status = TaskStatus.QUEUED if result.status == TaskStatus.QUEUED else TaskStatus.RUNNING
            snapshot = TaskResult(
                task_id=result.task_id,
                type=result.type,
                status=status,
                started_at=result.started_at,
                finished_at="",
            )
            return snapshot.to_dict()

    def _execute_task(self, task: Task, result: TaskResult) -> TaskResult:
        try:
            if task.type == TaskType.HEALTH_CHECK:
                result.data = self._read_health()
            else:
                self._connect_for_task()
                if task.type == TaskType.SEARCH_LEADS:
                    self._search_leads(task, result)
                elif task.type == TaskType.OPEN_COMPANY:
                    self._open_company(task, result)
                elif task.type == TaskType.GENERATE_SITE:
                    self._generate_site(task, result)
                else:
                    raise ValueError(f"Tipo de tarefa não suportado: {task.type}")
        except Exception as error:
            message = _safe_error_message(error)
            logger.error(
                "Falha ao executar tarefa V2 task_id=%s type=%s error=%s",
                task.task_id,
                task.type.value,
                message,
            )
            result.errors.append(message)
            if isinstance(error, (PlaywrightTimeoutError, TimeoutError)):
                result.status = TaskStatus.TIMEOUT
                result.error = {"code": "task_timeout", "message": message}
            else:
                result.status = TaskStatus.FAILED
                result.error = {"code": "task_failed", "message": message}
        else:
            if result.status == TaskStatus.RUNNING:
                result.status = TaskStatus.COMPLETED
        return result

    def health_check(self) -> dict[str, Any]:
        with _browser_execution_lock(self._browser_endpoint):
            return self._read_health()

    def _read_health(self) -> dict[str, Any]:
        health_reader = getattr(self.integration, "browser_health", None)
        if health_reader is None:
            return {
                "browser_connected": False,
                "edge_connected": False,
                "edge_product": None,
                "browser_version": None,
                "active_tab": None,
                "aivio_available": False,
                "aivio_hostname": None,
                "aivio_url": None,
                "cdp_endpoint": self._browser_endpoint,
                "expected_edge_profile": "EdgeProfile-V2",
                "operational": False,
                "browser_error": "O adaptador de browser não oferece health check.",
            }
        health = health_reader()
        if not isinstance(health, dict):
            raise RuntimeError("O health check retornou um formato inválido")
        health = dict(health)
        if isinstance(health.get("browser_error"), str):
            health["browser_error"] = _safe_error_message(RuntimeError(health["browser_error"]))
        return health

    def _connect_for_task(self) -> None:
        connect = getattr(self.browser_controller, "connect", None)
        if connect is not None:
            connect()

    def _search_leads(self, task: Task, result: TaskResult) -> None:
        payload = task.payload
        if not isinstance(payload, SearchLeadsPayload):
            raise TypeError("SEARCH_LEADS exige SearchLeadsPayload")
        filters = payload.filters
        warnings = [
            f"Filtro não suportado e não aplicado: {name}"
            for name in filters if name != "has_website"
        ]
        search = self.integration.search_leads(
            city=payload.city,
            state=payload.state,
            category=payload.category,
            quantity=payload.quantity,
            filters=filters,
            allow_credit_consumption=task.authorization.get("allow_credit_consumption") is True,
        )
        result.leads = [V2Lead.from_dict(item) for item in search["results"]]
        result.warnings.extend(warnings)
        result.warnings.extend(search.get("warnings", []))
        result.data = {
            "city": search["city"],
            "state": search["state"],
            "category": search["category"],
            "requested_quantity": search["requested_quantity"],
            "total": len(result.leads),
        }
        if search["status"] == "partial" or warnings:
            result.status = TaskStatus.PARTIAL

    def _open_company(self, task: Task, result: TaskResult) -> None:
        payload = task.payload
        if not isinstance(payload, LeadTaskPayload):
            raise TypeError(f"{task.type.value} exige LeadTaskPayload")
        updated = self.integration.open_company(
            payload.lead,
            allow_credit_consumption=task.authorization.get("allow_credit_consumption") is True,
        )
        result.leads = [updated]
        result.data = {"lead": updated.to_dict()}

    def _generate_site(self, task: Task, result: TaskResult) -> None:
        payload = task.payload
        if not isinstance(payload, LeadTaskPayload):
            raise TypeError("GENERATE_SITE exige LeadTaskPayload")
        updated, artifacts, warnings = self.integration.generate_site(
            payload.lead,
            allow_credit_consumption=task.authorization.get("allow_credit_consumption") is True,
            allow_external_effects=task.authorization.get("allow_external_effects") is True,
        )
        result.leads = [updated]
        result.data = {"lead": updated.to_dict()}
        result.artifacts = artifacts
        result.warnings.extend(warnings)
        website = next((item.get("url") for item in artifacts if item.get("type") == "website"), None)
        if isinstance(website, str):
            updated.website = website
            updated.has_website = True
            result.data["lead"] = updated.to_dict()
        if warnings:
            result.status = TaskStatus.PARTIAL
