import json
import os
import sys
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
