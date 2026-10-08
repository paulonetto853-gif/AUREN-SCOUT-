from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from threading import RLock
from typing import Any

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
        self._execution_lock = RLock()

    def execute(self, value: Task | dict[str, Any]) -> TaskResult:
        task = value if isinstance(value, Task) else Task.from_dict(value)
        with self._execution_lock:
            return self._execute_task(task)

    def _execute_task(self, task: Task) -> TaskResult:
        started_at = datetime.now(timezone.utc).isoformat()
        result = TaskResult(
            task_id=task.task_id,
            type=task.type,
            status=TaskStatus.COMPLETED,
            started_at=started_at,
            finished_at=started_at,
        )
        try:
            if task.type == TaskType.HEALTH_CHECK:
                result.data = self.health_check()
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
            logger.exception("Falha ao executar tarefa V2 task_id=%s type=%s", task.task_id, task.type.value)
            result.status = TaskStatus.FAILED
            result.errors.append(f"{type(error).__name__}: {error}")
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result

    def health_check(self) -> dict[str, Any]:
        with self._execution_lock:
            health_reader = getattr(self.integration, "browser_health", None)
            if health_reader is None:
                return {
                    "browser_connected": False,
                    "active_tab": None,
                    "aivio_available": False,
                    "browser_error": "O adaptador de browser não oferece health check.",
                }
            return health_reader()

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
        updated = self.integration.open_company(payload.lead)
        result.leads = [updated]
        result.data = {"lead": updated.to_dict()}

    def _generate_site(self, task: Task, result: TaskResult) -> None:
        payload = task.payload
        if not isinstance(payload, LeadTaskPayload):
            raise TypeError("GENERATE_SITE exige LeadTaskPayload")
        updated, artifacts, warnings = self.integration.generate_site(payload.lead)
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
