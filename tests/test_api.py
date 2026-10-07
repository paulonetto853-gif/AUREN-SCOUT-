import json
import threading
import unittest
import urllib.error
import urllib.request

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

    def test_server_rejects_non_loopback_bind_addresses(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1"):
            create_server(ScoutService(MockSearchProvider()), host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()