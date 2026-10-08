import json
import unittest
from unittest.mock import patch

from scout.browser_controller import BrowserController


class FakePage:
    def __init__(self, title, url, active=False):
        self._title = title
        self.url = url
        self.active = active
        self.navigated_to = None

    def is_closed(self):
        return False

    def title(self):
        return self._title

    def evaluate(self, expression):
        self.asserted_expression = expression
        return self.active

    def goto(self, url, wait_until):
        self.navigated_to = (url, wait_until)
        self.url = url
        self._title = "Destino"

    def screenshot(self):
        return b"image"


class FakeBrowserSession:
    def __init__(self, product):
        self.product = product
        self.detached = False

    def send(self, method):
        self.method = method
        return {"product": self.product}

    def detach(self):
        self.detached = True


class FakeBrowser:
    def __init__(self, product, pages):
        self.session = FakeBrowserSession(product)
        self.contexts = [type("Context", (), {"pages": pages})()]
        self.connected = True

    def is_connected(self):
        return self.connected

    def new_browser_cdp_session(self):
        return self.session


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = type(
            "Chromium",
            (),
            {"connect_over_cdp": lambda _, endpoint, **kwargs: browser},
        )()
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


class BrowserControllerTests(unittest.TestCase):
    def setUp(self):
        self.inactive_page = FakePage("AIVIO", "https://aivio.example/", active=False)
        self.active_page = FakePage("AIVIO - Painel", "https://aivio.example/home", active=True)
        self.browser = FakeBrowser("Edg/130.0.0.0", [self.inactive_page, self.active_page])
        self.playwright = FakePlaywright(self.browser)
        self.manager = FakePlaywrightManager(self.playwright)
        self.controller = BrowserController("http://127.0.0.1:9222")

    def connect(self):
        with patch("scout.browser_controller.sync_playwright", return_value=self.manager):
            self.controller.connect()

    def test_connect_status_lists_tabs_and_identifies_active_tab_as_json(self):
        self.connect()

        status = self.controller.getStatus()
        decoded = json.loads(json.dumps(status))

        self.assertTrue(decoded["connected"])
        self.assertEqual(decoded["browser"], "Microsoft Edge")
        self.assertEqual(decoded["tabs"], [
            {"title": "AIVIO", "url": "https://aivio.example/"},
            {"title": "AIVIO - Painel", "url": "https://aivio.example/home"},
        ])
        self.assertEqual(decoded["activeTab"], {
            "title": "AIVIO - Painel",
            "url": "https://aivio.example/home",
        })
        self.assertEqual(self.browser.session.method, "Browser.getVersion")
        self.assertTrue(self.browser.session.detached)
        self.controller.disconnect()
        self.assertTrue(self.playwright.stopped)
        self.assertTrue(self.browser.connected)

    def test_reconnect_stops_stale_playwright_after_edge_disconnects(self):
        self.connect()
        old_playwright = self.playwright
        self.browser.connected = False
        replacement_browser = FakeBrowser("Edg/131.0.0.0", [self.active_page])
        replacement_playwright = FakePlaywright(replacement_browser)
        self.manager.playwright = replacement_playwright

        with patch("scout.browser_controller.sync_playwright", return_value=self.manager):
            self.controller.connect()

        self.assertTrue(old_playwright.stopped)
        self.assertIs(self.controller._browser, replacement_browser)

    def test_navigation_only_navigates_the_active_tab(self):
        self.connect()

        result = self.controller.navigate("https://example.com/path")

        self.assertEqual(result, {"title": "Destino", "url": "https://example.com/path"})
        self.assertIsNone(self.inactive_page.navigated_to)
        self.assertEqual(self.active_page.navigated_to, ("https://example.com/path", "domcontentloaded"))

    def test_navigation_rejects_non_http_urls_and_embedded_credentials(self):
        for url in ("javascript:alert(1)", "file:///etc/passwd", "https://user:pass@example.com/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.controller.navigate(url)

    def test_endpoint_is_required_and_must_not_contain_credentials(self):
        with self.assertRaisesRegex(RuntimeError, "SCOUT_CDP_ENDPOINT"):
            BrowserController("").connect()
        with self.assertRaises(ValueError):
            BrowserController("http://user:pass@127.0.0.1:9222").connect()

    def test_non_edge_cdp_endpoint_is_rejected_and_disconnected(self):
        self.browser.session.product = "Chrome/130.0.0.0"
        with patch("scout.browser_controller.sync_playwright", return_value=self.manager):
            with self.assertRaisesRegex(RuntimeError, "não pertence ao Microsoft Edge"):
                self.controller.connect()
        self.assertTrue(self.playwright.stopped)
        self.assertIsNone(self.controller._browser)

    def test_read_page_info_and_screenshot_use_active_tab(self):
        self.connect()

        self.assertEqual(self.controller.readPageInfo(), {
            "title": "AIVIO - Painel",
            "url": "https://aivio.example/home",
        })
        self.assertEqual(self.controller.screenshot(), b"image")


if __name__ == "__main__":
    unittest.main()
