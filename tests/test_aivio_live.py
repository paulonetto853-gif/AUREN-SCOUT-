import json
import os
import unittest

from scout.aivio import AivioIntegration, CDP_ENDPOINT
from scout.browser_controller import BrowserController


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


if __name__ == "__main__":
    unittest.main()
