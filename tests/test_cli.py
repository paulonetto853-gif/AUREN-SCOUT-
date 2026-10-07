import os
import sys
import unittest
from unittest.mock import patch

from scout.cli import main


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


if __name__ == "__main__":
    unittest.main()
