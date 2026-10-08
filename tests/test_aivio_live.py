import json
import os
import unittest
import uuid

from scout.aivio import AivioIntegration, CDP_ENDPOINT
from scout.browser_controller import BrowserController
from scout.task_executor import TaskExecutor


@unittest.skipUnless(
    os.getenv("AIVIO_LIVE_TEST") == "1" and os.getenv("AIVIO_TEST_CITY", "").strip(),
    "set AIVIO_LIVE_TEST=1 and AIVIO_TEST_CITY to run against the manually opened AIVIO page",
)
class LiveAivioIntegrationTests(unittest.TestCase):
    def test_searches_the_active_aivio_page(self):
        controller = BrowserController(cdp_endpoint=CDP_ENDPOINT)
        try:
            controller.connect()
            result = AivioIntegration(controller).search_city(
                os.environ["AIVIO_TEST_CITY"],
                open_first_company=os.getenv("AIVIO_OPEN_FIRST_COMPANY") == "1",
            )
        finally:
            controller.disconnect()

        encoded_result = json.loads(json.dumps(result, ensure_ascii=False))
        self.assertEqual(encoded_result["status"], "completed")
        self.assertEqual(encoded_result["city"], os.environ["AIVIO_TEST_CITY"].strip())
        self.assertIsInstance(encoded_result["results"], list)
        if os.getenv("AIVIO_OPEN_FIRST_COMPANY") == "1":
            self.assertIsNotNone(encoded_result["companyDetails"])


@unittest.skipUnless(
    os.getenv("SCOUT_AIVIO_LIVE_TEST") == "1"
    and os.getenv("SCOUT_AIVIO_LIVE_TASK", "").strip()
    and os.getenv("SCOUT_AIVIO_LIVE_PAYLOAD", "").strip()
    and (
        os.getenv("SCOUT_AIVIO_LIVE_TASK") != "GENERATE_SITE"
        or os.getenv("SCOUT_AIVIO_LIVE_ALLOW_GENERATE") == "1"
    ),
    "configure SCOUT_AIVIO_LIVE_TEST, task/payload; generation additionally requires explicit allow",
)
class LiveV2TaskTests(unittest.TestCase):
    def test_executes_explicit_v2_task_against_aivio(self):
        task_type = os.environ["SCOUT_AIVIO_LIVE_TASK"]
        payload = json.loads(os.environ["SCOUT_AIVIO_LIVE_PAYLOAD"])
        result = TaskExecutor().execute({
            "task_id": str(uuid.uuid4()),
            "type": task_type,
            "payload": payload,
        })
        self.assertNotEqual(result.status.value, "failed", msg="; ".join(result.errors))
        self.assertTrue(result.finished_at)


if __name__ == "__main__":
    unittest.main()
