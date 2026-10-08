import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

from scout.browser_operator import BrowserOperator, BrowserOperatorExecutor
from scout.service import ScoutService
from scout.task_executor import TaskExecutor, _safe_error_message
from scout.v2_models import Task, TaskStatus

logger = logging.getLogger("scout.api")
MAX_REQUEST_BYTES = 64_000


def create_server(
    service: ScoutService,
    host: str = "127.0.0.1",
    port: int = 8080,
    task_executor: TaskExecutor | None = None,
    operator_executor: BrowserOperatorExecutor | None = None,
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("a API só pode escutar em 127.0.0.1")
    executor_holder = [task_executor]
    executor_lock = Lock()
    operator_holder = [operator_executor]
    operator_lock = Lock()

    def get_task_executor() -> TaskExecutor:
        with executor_lock:
            if executor_holder[0] is None:
                executor_holder[0] = TaskExecutor()
            return executor_holder[0]

    def get_operator_executor() -> BrowserOperatorExecutor:
        with operator_lock:
            if operator_holder[0] is None:
                operator_holder[0] = BrowserOperatorExecutor()
            return operator_holder[0]

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/health":
                health = {
                    "status": "ok",
                    "api_operational": True,
                    "operational": False,
                    "provider": service.provider.__class__.__name__,
                }
                try:
                    health.update(get_task_executor().health_check())
                except Exception as error:
                    logger.exception("Falha ao consultar estado do browser para health check")
                    health.update({
                        "browser_connected": False,
                        "edge_connected": False,
                        "edge_product": None,
                        "browser_version": None,
                        "active_tab": None,
                        "aivio_available": False,
                        "aivio_hostname": None,
                        "aivio_url": None,
                        "cdp_endpoint": None,
                        "expected_edge_profile": "EdgeProfile-V2",
                        "browser_error": _safe_error_message(error),
                    })
                self._json(200, health)
                return
            task_prefix = "/tasks/"
            task_id = path[len(task_prefix):] if path.startswith(task_prefix) else ""
            if task_id and "/" not in task_id:
                result = get_task_executor().get_task_result(task_id)
                if result is not None:
                    self._json(200, result)
                else:
                    self._json(404, {"error": "task_not_found"})
                return
            operator_prefix = "/operator/tasks/"
            operator_task_id = (
                path[len(operator_prefix):] if path.startswith(operator_prefix) else ""
            )
            if operator_task_id and "/" not in operator_task_id:
                result = get_operator_executor().get_result(operator_task_id)
                if result is not None:
                    self._json(200, result)
                else:
                    self._json(404, {"error": "task_not_found"})
                return
            prefix = "/scout/lead/"
            if path.startswith(prefix) and path[len(prefix):] and "/" not in path[len(prefix):]:
                lead = service.store.get(path[len(prefix):])
                if lead:
                    self._json(200, lead.to_dict())
                else:
                    self._json(404, {"error": "lead_not_found"})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path == "/tasks":
                self._handle_task()
                return
            if path == "/operator/tasks":
                self._handle_operator_task()
                return
            if path != "/scout/search":
                self._json(404, {"error": "not_found"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 1 or content_length > MAX_REQUEST_BYTES:
                    raise ValueError("tamanho de requisição inválido")
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict):
                    raise ValueError("corpo deve ser um objeto JSON")
                query, city, state = (payload.get(key) for key in ("query", "city", "state"))
                if not all(isinstance(value, str) and value.strip() for value in (query, city, state)):
                    raise ValueError("query, city e state são obrigatórios")
                limit = payload.get("limit", 20)
                if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                    raise ValueError("limit deve ser um inteiro entre 1 e 100")
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self._json(400, {"error": "invalid_request", "message": str(error)})
                return

            try:
                result = service.search(query.strip(), city.strip(), state.strip(), limit)
            except Exception:
                logger.exception("Falha na pesquisa solicitada pela API")
                self._json(500, {"error": "search_failed"})
                return
            self._json(200, {
                "results": [lead.to_dict() for lead in result.results],
                "total": result.total,
                "errors": result.errors,
            })

        def _handle_task(self) -> None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 1 or content_length > MAX_REQUEST_BYTES:
                    raise ValueError("tamanho de requisição inválido")
                payload = json.loads(self.rfile.read(content_length))
                task = Task.from_dict(payload)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self._json(400, {"error": "invalid_task", "message": str(error)})
                return
            try:
                result = get_task_executor().execute(task)
            except ValueError as error:
                self._json(409, {"error": "duplicate_task", "message": str(error)})
                return
            except Exception as error:
                logger.exception("Falha ao iniciar tarefa recebida pela API")
                self._json(503, {"error": "task_service_unavailable", "message": str(error)})
                return
            status_code = 504 if result.status == TaskStatus.TIMEOUT else (
                500 if result.status == TaskStatus.FAILED else 200
            )
            self._json(status_code, result.to_dict())

        def _handle_operator_task(self) -> None:
            payload: Any = None
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 1 or content_length > MAX_REQUEST_BYTES:
                    raise ValueError("tamanho de requisição inválido")
                payload = json.loads(self.rfile.read(content_length))
                task = BrowserOperator.validate_task(payload)
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
                self._json(400, {
                    "task_id": payload.get("task_id") if isinstance(payload, dict) else None,
                    "status": "failed",
                    "action": None,
                    "result": None,
                    "evidence": [],
                    "error": {"code": "invalid_task", "message": str(error)},
                })
                return
            try:
                result = get_operator_executor().execute(task)
            except Exception:
                logger.exception("Falha ao executar tarefa do operador web V2")
                self._json(503, {
                    "task_id": task["task_id"],
                    "status": "failed",
                    "action": None,
                    "result": None,
                    "evidence": [],
                    "error": {
                        "code": "operator_unavailable",
                        "message": "O operador web V2 não conseguiu iniciar a tarefa",
                    },
                })
                return
            error_code = result.get("error", {}).get("code")
            status_code = (
                504 if error_code == "operation_timeout"
                else 409 if error_code == "duplicate_task"
                else 500 if result["status"] == "failed"
                else 200
            )
            self._json(status_code, result)

        def log_message(self, format: str, *args: object) -> None:
            logger.info("API " + format, *args)

    return ThreadingHTTPServer((host, port), Handler)