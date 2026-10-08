import argparse
import json
import logging
import os
import uuid

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from scout.api import create_server
from scout.aivio import CDP_ENDPOINT
from scout.browser_controller import BrowserController
from scout.discovery import MockSearchProvider
from scout.service import ScoutService
from scout.task_executor import TaskExecutor
from scout.v2_models import TaskStatus, TaskType


def main() -> None:
    parser = argparse.ArgumentParser(prog="auren-scout")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="iniciar a API HTTP")
    serve.add_argument("--host", default=os.getenv("SCOUT_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("SCOUT_PORT", "8080")))
    commands.add_parser("browser-status", help="consultar o estado do Edge conectado por CDP")
    commands.add_parser("browser-inspect", help="inspecionar a página ativa sem interagir com ela")
    commands.add_parser(
        "browser-inspect-category",
        help="abrir e inspecionar o dropdown de categoria sem selecionar uma opção",
    )
    commands.add_parser(
        "browser-test-city",
        help="diagnosticar o autocomplete de cidade sem selecionar uma sugestão",
    )
    navigate = commands.add_parser("browser-navigate", help="navegar a aba ativa para uma URL autorizada")
    navigate.add_argument("url")
    task = commands.add_parser("task", help="executar uma tarefa V2 do AUREN no AIVIO")
    task.add_argument("type", choices=[task_type.value for task_type in TaskType])
    task.add_argument("--payload", required=True, help="objeto JSON com os dados da tarefa")
    task.add_argument("--task-id", default=None, help="UUID opcional da tarefa")
    task.add_argument(
        "--authorization",
        default="{}",
        help="objeto JSON de permissões explícitas para efeitos e consumo de créditos",
    )
    args = parser.parse_args()

    if args.command is None:
        args.command = "serve"
        args.host = os.getenv("SCOUT_HOST", "127.0.0.1")
        args.port = int(os.getenv("SCOUT_PORT", "8080"))

    logging.basicConfig(level=os.getenv("SCOUT_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command in {
        "browser-status",
        "browser-inspect",
        "browser-inspect-category",
        "browser-test-city",
        "browser-navigate",
    }:
        cdp_endpoint = os.getenv("SCOUT_CDP_ENDPOINT", CDP_ENDPOINT)
        if cdp_endpoint != CDP_ENDPOINT:
            parser.error(f"Os comandos browser-* da V2 aceitam somente {CDP_ENDPOINT}")
        controller = BrowserController(cdp_endpoint=cdp_endpoint)
        if args.command == "browser-test-city":
            result = {
                "connected": False,
                "city_input_found": False,
                "typed": "PORTO",
                "suggestions": [],
                "porto_alegre_found": False,
                "exact_match_count": 0,
            }
            operation_error: Exception | None = None
            try:
                controller.connect()
                result["connected"] = True
                if controller.element_visible(placeholder="Digite uma cidade..."):
                    result["city_input_found"] = True
                    controller.click_element(placeholder="Digite uma cidade...")
                    controller.fill_input("PORTO", placeholder="Digite uma cidade...")
                    try:
                        controller.wait_for_text("Porto Alegre", timeout_ms=5_000)
                    except PlaywrightTimeoutError:
                        pass

                    visible_text = controller.read_visible_text()
                    suggestions = [
                        line.strip()
                        for line in visible_text.splitlines()
                        if line.strip().casefold().startswith("porto")
                    ]
                    exact_match_count = sum(
                        suggestion.casefold() == "porto alegre"
                        for suggestion in suggestions
                    )
                    result["suggestions"] = suggestions
                    result["porto_alegre_found"] = exact_match_count > 0
                    result["exact_match_count"] = exact_match_count
            except (PlaywrightError, RuntimeError, ValueError) as error:
                operation_error = error
            finally:
                controller.disconnect()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if operation_error is not None:
                logging.getLogger("scout.cli").error(
                    "browser-test-city falhou (%s)", type(operation_error).__name__
                )
                raise SystemExit(1)
            if not result["connected"] or not result["city_input_found"]:
                raise SystemExit(1)
            return
        try:
            controller.connect()
            if args.command == "browser-status":
                result = controller.getStatus()
            elif args.command == "browser-inspect":
                result = controller.inspectPage()
            elif args.command == "browser-inspect-category":
                result = controller.inspectCategoryDropdown()
            else:
                result = controller.navigate(args.url)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except (PlaywrightError, RuntimeError, ValueError) as error:
            parser.error(str(error))
        finally:
            controller.disconnect()
        return
    if args.command == "task":
        try:
            payload = json.loads(args.payload)
            if not isinstance(payload, dict):
                raise ValueError("--payload deve conter um objeto JSON")
            authorization = json.loads(args.authorization)
            if not isinstance(authorization, dict):
                raise ValueError("--authorization deve conter um objeto JSON")
            task_request = {
                "task_id": args.task_id or str(uuid.uuid4()),
                "type": args.type,
                "payload": payload,
                "authorization": authorization,
            }
            result = TaskExecutor().execute(task_request)
        except (ValueError, json.JSONDecodeError) as error:
            parser.error(str(error))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.status in {TaskStatus.FAILED, TaskStatus.TIMEOUT}:
            raise SystemExit(1)
        return

    provider = os.getenv("SCOUT_PROVIDER", "mock").casefold()
    if provider != "mock":
        parser.error("somente SCOUT_PROVIDER=mock está disponível nesta versão")
    if args.host != "127.0.0.1":
        parser.error("a API só pode escutar em 127.0.0.1")
    try:
        server = create_server(ScoutService(MockSearchProvider()), args.host, args.port)
    except ValueError as error:
        parser.error(str(error))
    logging.getLogger("scout").info("API Scout disponível em http://%s:%s", *server.server_address)
    logging.getLogger("scout").info("Auren Scout operacional. Health check: http://127.0.0.1:%s/health",
                                    server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.getLogger("scout").info("Encerrando API Scout")
    finally:
        server.server_close()