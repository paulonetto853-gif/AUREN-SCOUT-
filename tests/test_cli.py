import json
import os
import sys
import unittest
from unittest.mock import patch

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scout.aivio import CDP_ENDPOINT
from scout.cli import main
from scout.v2_models import TaskResult, TaskStatus, TaskType


class FakeServer:
    server_address = ("127.0.0.1", 8080)

    def __init__(self):
        self.served = False
        self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


class CliTests(unittest.TestCase):
    def test_no_arguments_starts_the_existing_api_on_loopback(self):
        server = FakeServer()

        with (
            patch.object(sys, "argv", ["auren-scout"]),
            patch.dict(os.environ, {"SCOUT_HOST": "127.0.0.1", "SCOUT_PORT": "8080"}, clear=False),
            patch("scout.cli.create_server", return_value=server) as create_server,
        ):
            main()

        self.assertEqual(create_server.call_args.args[1:], ("127.0.0.1", 8080))
        self.assertTrue(server.served)
        self.assertTrue(server.closed)

    def test_non_loopback_host_is_rejected(self):
        with (
            patch.object(sys, "argv", ["auren-scout"]),
            patch.dict(os.environ, {"SCOUT_HOST": "0.0.0.0"}, clear=False),
            patch("scout.cli.create_server") as create_server,
            self.assertRaises(SystemExit),
        ):
            main()

        create_server.assert_not_called()

    def test_v2_browser_status_defaults_to_its_own_endpoint(self):
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-status"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print"),
        ):
            controller.return_value.getStatus.return_value = {"connected": True}
            main()

        controller.assert_called_once_with(cdp_endpoint=CDP_ENDPOINT)
        controller.return_value.connect.assert_called_once_with()
        controller.return_value.disconnect.assert_called_once_with()

    def test_v2_browser_status_accepts_its_explicit_endpoint(self):
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-status"]),
            patch.dict(os.environ, {"SCOUT_CDP_ENDPOINT": CDP_ENDPOINT}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print"),
        ):
            controller.return_value.getStatus.return_value = {"connected": True}
            main()

        controller.assert_called_once_with(cdp_endpoint=CDP_ENDPOINT)

    def test_v2_browser_status_rejects_the_v1_endpoint(self):
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-status"]),
            patch.dict(os.environ, {"SCOUT_CDP_ENDPOINT": "http://127.0.0.1:9222"}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            self.assertRaises(SystemExit),
        ):
            main()

        controller.assert_not_called()

    def test_browser_inspect_prints_valid_json_without_interaction(self):
        inspection = {
            "connected": True,
            "title": "AIVIO Dashboard",
            "url": "https://aivio.example/dashboard",
            "text": "Página atual",
            "inputs": [],
            "buttons": [],
            "links": [],
        }
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-inspect"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print") as print_output,
        ):
            controller.return_value.inspectPage.return_value = inspection
            main()

        output = json.loads(print_output.call_args.args[0])
        self.assertEqual(output, inspection)
        controller.assert_called_once_with(cdp_endpoint=CDP_ENDPOINT)
        controller.return_value.connect.assert_called_once_with()
        controller.return_value.inspectPage.assert_called_once_with()
        controller.return_value.disconnect.assert_called_once_with()
        controller.return_value.navigate.assert_not_called()

    def test_browser_inspect_category_prints_json_and_calls_diagnostic_only(self):
        inspection = {
            "connected": True,
            "title": "AIVIO Dashboard",
            "url": "https://aivio.example/dashboard",
            "inputs_before_open": [],
            "category_button": {"text": "Escolha o ramo"},
            "dropdown_opened": True,
            "new_elements": [{"tag": "div", "text": "Restaurantes", "role": "option"}],
            "element_limit_reached": False,
        }
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-inspect-category"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print") as print_output,
        ):
            controller.return_value.inspectCategoryDropdown.return_value = inspection
            main()

        self.assertEqual(json.loads(print_output.call_args.args[0]), inspection)
        controller.assert_called_once_with(cdp_endpoint=CDP_ENDPOINT)
        controller.return_value.inspectCategoryDropdown.assert_called_once_with()
        controller.return_value.navigate.assert_not_called()

    def test_browser_test_city_reports_autocomplete_without_selecting_suggestion(self):
        visible_text = "Digite uma cidade...\nPorto\nPorto Alegre\nPorto Seguro-BA\nPorto Velho-RO"
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-test-city"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("scout.cli.TaskExecutor") as executor,
            patch("builtins.print") as print_output,
        ):
            controller.return_value.read_visible_text.return_value = visible_text
            main()

        output = json.loads(print_output.call_args.args[0])
        self.assertEqual(
            output,
            {
                "connected": True,
                "city_input_found": True,
                "typed": "PORTO",
                "suggestions": [
                    "Porto",
                    "Porto Alegre",
                    "Porto Seguro-BA",
                    "Porto Velho-RO",
                ],
                "porto_alegre_found": True,
                "exact_match_count": 1,
            },
        )
        instance = controller.return_value
        controller.assert_called_once_with(cdp_endpoint=CDP_ENDPOINT)
        instance.connect.assert_called_once_with()
        instance.element_visible.assert_called_once_with(
            placeholder="Digite uma cidade..."
        )
        instance.click_element.assert_called_once_with(
            placeholder="Digite uma cidade..."
        )
        instance.fill_input.assert_called_once_with(
            "PORTO", placeholder="Digite uma cidade..."
        )
        instance.wait_for_text.assert_called_once_with("Porto Alegre", timeout_ms=5_000)
        instance.read_visible_text.assert_called_once_with()
        instance.disconnect.assert_called_once_with()
        instance.click_text.assert_not_called()
        instance.click_button.assert_not_called()
        instance.open_dropdown.assert_not_called()
        instance.select_option.assert_not_called()
        instance.navigate.assert_not_called()
        executor.assert_not_called()

    def test_browser_test_city_prints_connection_failure_as_json(self):
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-test-city"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print") as print_output,
            self.assertRaises(SystemExit) as context,
        ):
            controller.return_value.connect.side_effect = RuntimeError("CDP unavailable")
            main()

        self.assertEqual(context.exception.code, 1)
        self.assertEqual(
            json.loads(print_output.call_args.args[0]),
            {
                "connected": False,
                "city_input_found": False,
                "typed": "PORTO",
                "suggestions": [],
                "porto_alegre_found": False,
                "exact_match_count": 0,
            },
        )
        controller.return_value.disconnect.assert_called_once_with()
        controller.return_value.element_visible.assert_not_called()

    def test_browser_test_city_reports_missing_city_input(self):
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-test-city"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print") as print_output,
            self.assertRaises(SystemExit) as context,
        ):
            controller.return_value.element_visible.return_value = False
            main()

        self.assertEqual(context.exception.code, 1)
        output = json.loads(print_output.call_args.args[0])
        self.assertTrue(output["connected"])
        self.assertFalse(output["city_input_found"])
        self.assertEqual(output["suggestions"], [])
        controller.return_value.click_element.assert_not_called()
        controller.return_value.fill_input.assert_not_called()

    def test_browser_test_city_returns_false_when_exact_suggestion_is_missing(self):
        with (
            patch.object(sys, "argv", ["AurenScout-V2.exe", "browser-test-city"]),
            patch.dict(os.environ, {}, clear=True),
            patch("scout.cli.BrowserController") as controller,
            patch("builtins.print") as print_output,
        ):
            controller.return_value.wait_for_text.side_effect = PlaywrightTimeoutError(
                "suggestion timeout"
            )
            controller.return_value.read_visible_text.return_value = (
                "Porto\nPorto Seguro-BA\nPorto Velho-RO"
            )
            main()

        output = json.loads(print_output.call_args.args[0])
        self.assertEqual(output["suggestions"], ["Porto", "Porto Seguro-BA", "Porto Velho-RO"])
        self.assertFalse(output["porto_alegre_found"])
        self.assertEqual(output["exact_match_count"], 0)
        controller.return_value.read_visible_text.assert_called_once_with()

    def test_task_command_executes_and_prints_standardized_result(self):
        result = TaskResult(
            task_id="12345678-1234-5678-1234-567812345678",
            type=TaskType.HEALTH_CHECK,
            status=TaskStatus.COMPLETED,
        )
        with (
            patch.object(sys, "argv", ["auren-scout", "task", "HEALTH_CHECK", "--payload", "{}"]),
            patch("scout.cli.TaskExecutor") as executor_class,
        ):
            executor_class.return_value.execute.return_value = result
            with patch("builtins.print") as print_result:
                main()
        printed = json.loads(print_result.call_args.args[0])
        self.assertEqual(printed["type"], "HEALTH_CHECK")
        self.assertEqual(printed["status"], "completed")

    def test_task_command_returns_nonzero_for_timeout(self):
        result = TaskResult(
            task_id="12345678-1234-5678-1234-567812345678",
            type=TaskType.HEALTH_CHECK,
            status=TaskStatus.TIMEOUT,
        )
        with (
            patch.object(
                sys, "argv",
                ["auren-scout", "task", "HEALTH_CHECK", "--payload", "{}"],
            ),
            patch("scout.cli.TaskExecutor") as executor_class,
            patch("builtins.print"),
        ):
            executor_class.return_value.execute.return_value = result
            with self.assertRaises(SystemExit) as context:
                main()
        self.assertEqual(context.exception.code, 1)

    def test_task_command_forwards_explicit_authorization(self):
        result = TaskResult(
            task_id="12345678-1234-5678-1234-567812345678",
            type=TaskType.SEARCH_LEADS,
            status=TaskStatus.COMPLETED,
        )
        with (
            patch.object(
                sys,
                "argv",
                [
                    "auren-scout",
                    "task",
                    "SEARCH_LEADS",
                    "--authorization",
                    '{"allow_credit_consumption":true}',
                    "--payload",
                    '{"city":"Porto Alegre","state":"RS","category":"restaurantes"}',
                ],
            ),
            patch("scout.cli.TaskExecutor") as executor_class,
            patch("builtins.print"),
        ):
            executor_class.return_value.execute.return_value = result
            main()
        request = executor_class.return_value.execute.call_args.args[0]
        self.assertTrue(request["authorization"]["allow_credit_consumption"])


if __name__ == "__main__":
    unittest.main()
