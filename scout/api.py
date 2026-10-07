import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from scout.service import ScoutService

logger = logging.getLogger("scout.api")
MAX_REQUEST_BYTES = 64_000


def create_server(service: ScoutService, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("a API só pode escutar em 127.0.0.1")

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
                self._json(200, {
                    "status": "ok",
                    "operational": True,
                    "provider": service.provider.__class__.__name__,
                })
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
            if urlsplit(self.path).path != "/scout/search":
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

        def log_message(self, format: str, *args: object) -> None:
            logger.info("API " + format, *args)

    return ThreadingHTTPServer((host, port), Handler)