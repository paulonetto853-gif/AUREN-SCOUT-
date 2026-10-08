import argparse
import json
import logging
import os
import uuid

from playwright.sync_api import Error as PlaywrightError

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
    navigate = commands.add_parser("browser-navigate", help="navegar a aba ativa para uma URL autorizada")
    navigate.add_argument("url")
    task = commands.add_parser("task", help="executar uma tarefa V2 do AUREN no AIVIO")
    task.add_argument("type", choices=[task_type.value for task_type in TaskType])
    task.add_argument("--payload", required=True, help="objeto JSON com os dados da tarefa")
    task.add_argument("--task-id", default=None, help="UUID opcional da tarefa")
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
        "browser-navigate",
    }:
        cdp_endpoint = os.getenv("SCOUT_CDP_ENDPOINT", CDP_ENDPOINT)
        if cdp_endpoint != CDP_ENDPOINT:
            parser.error(f"Os comandos browser-* da V2 aceitam somente {CDP_ENDPOINT}")
        controller = BrowserController(cdp_endpoint=cdp_endpoint)
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
            task_request = {
                "task_id": args.task_id or str(uuid.uuid4()),
                "type": args.type,
                "payload": payload,
            }
            result = TaskExecutor().execute(task_request)
        except (ValueError, json.JSONDecodeError) as error:
            parser.error(str(error))
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        if result.status == TaskStatus.FAILED:
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