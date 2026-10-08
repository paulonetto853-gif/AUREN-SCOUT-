import json
import os
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from unittest.mock import patch

from scout.api import create_server
from scout.discovery import MockSearchProvider
from scout.service import ScoutService


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(ScoutService(MockSearchProvider()), port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, payload):
        request = urllib.request.Request(
            f"{self.base_url}/scout/search", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        return response.code, json.loads(response.read())

    def post_task(self, payload):
        request = urllib.request.Request(
            f"{self.base_url}/tasks", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        return response.code, json.loads(response.read())

    def test_search_response_is_structured_sorted_and_retrievable(self):
        status, payload = self.post({
            "query": "dentistas", "city": "Porto Alegre", "state": "RS", "limit": 20,
        })

        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["errors"], 0)
        self.assertEqual(payload["results"][0]["companyName"], "Clínica Exemplo Porto Alegre")
        self.assertEqual(payload["results"][0]["source"], "mock://catalog")
        self.assertEqual(payload["results"][0]["websiteStatus"], "not_verified")
        self.assertIsNone(payload["results"][0]["hasWebsite"])
        self.assertIsNone(payload["results"][0]["phone"])

        lead_id = payload["results"][0]["id"]
        with urllib.request.urlopen(f"{self.base_url}/scout/lead/{lead_id}") as response:
            lead = json.loads(response.read())
        self.assertEqual(lead["id"], lead_id)

    def test_invalid_search_request_returns_400(self):
        status, payload = self.post({"query": "dentistas"})

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_request")

    def test_health_endpoint(self):
        with urllib.request.urlopen(f"{self.base_url}/health") as response:
            payload = json.loads(response.read())
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["operational"])
        self.assertFalse(payload["browser_connected"])
        self.assertFalse(payload["aivio_available"])

    def test_tasks_endpoint_runs_health_task_with_standard_result(self):
        status, payload = self.post_task({
            "task_id": str(uuid.uuid4()),
            "type": "HEALTH_CHECK",
            "payload": {},
        })
        self.assertEqual(status, 200)
        self.assertEqual(payload["type"], "HEALTH_CHECK")
        self.assertEqual(payload["status"], "completed")
        self.assertFalse(payload["data"]["browser_connected"])
        self.assertEqual(payload["errors"], [])

    def test_tasks_endpoint_rejects_invalid_task(self):
        status, payload = self.post_task({"task_id": "invalid", "type": "NOPE", "payload": {}})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid_task")

    def test_server_rejects_non_loopback_bind_addresses(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_server(ScoutService(MockSearchProvider()), host="0.0.0.0", port=0)

    def test_legacy_server_creation_does_not_require_v2_cdp_configuration(self):
        with patch.dict(os.environ, {"SCOUT_CDP_ENDPOINT": "https://remote.example:9222"}):
            server = create_server(ScoutService(MockSearchProvider()), port=0)
        server.server_close()


if __name__ == "__main__":
    unittest.main()