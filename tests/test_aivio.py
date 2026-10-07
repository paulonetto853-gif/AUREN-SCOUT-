import unittest

from scout.aivio import (
    AivioIntegration,
    CITY_INPUTS_SCRIPT,
    DETAIL_LINKS_SCRIPT,
    PAGE_TEXT_SCRIPT,
    SEARCH_RESULTS_SCRIPT,
)


class FakeLocator:
    def __init__(self, *, items=None, visible=True, click_action=None):
        self.items = items or []
        self.visible = visible
        self.click_action = click_action
        self.filled = None
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return len(self.items) if self.items else int(self.visible)

    def nth(self, index):
        self.index = index
        return self

    def fill(self, value):
        self.filled = value

    def click(self):
        self.clicked = True
        if self.click_action:
            self.click_action()

    def evaluate_all(self, script):
        self.asserted_script = script
        return self.items


class FakePage:
    def __init__(self, *, url="https://app.aivio.example/search", title="AIVIO - Pesquisa"):
        self.url = url
        self._title = title
        self.body_text = "Pesquisa de empresas"
        self.city_input = FakeLocator(items=[{
            "index": 0,
            "visible": True,
            "disabled": False,
            "descriptors": "cidade",
        }])
        self.search_button = FakeLocator(click_action=self._complete_search)
        self.search_results = [{
            "title": "Restaurante Exemplo",
            "text": "Restaurante Exemplo\nCentro, Porto Alegre",
            "url": "https://app.aivio.example/company/123",
            "elementType": "card",
        }]
        self.detail_links = [{"title": "Site", "url": "https://example.invalid/"}]
        self.navigated_to = None

    def title(self):
        return self._title

    def locator(self, selector):
        if selector != 'input:not([type="hidden"]), textarea':
            raise AssertionError(f"unexpected selector: {selector}")
        return self.city_input

    def evaluate(self, script, *args, **kwargs):
        if script == PAGE_TEXT_SCRIPT:
            return self.body_text
        if script == SEARCH_RESULTS_SCRIPT:
            return self.search_results
        if script == DETAIL_LINKS_SCRIPT:
            return self.detail_links
        raise AssertionError(f"unexpected page script: {script}")

    def get_by_role(self, role, name):
        if role != "button":
            raise AssertionError(f"unexpected role: {role}")
        self.search_button_name = name
        return self.search_button

    def get_by_text(self, text, exact):
        if (text, exact) != ("Ver agora", True):
            raise AssertionError("unexpected text locator")
        return FakeLocator(visible=False)

    def wait_for_function(self, expression, **kwargs):
        self.waited_for_change = (expression, kwargs)

    def wait_for_load_state(self, state):
        self.waited_for_load = state

    def goto(self, url, wait_until):
        self.navigated_to = (url, wait_until)
        self.url = url
        self._title = "Restaurante Exemplo | AIVIO"
        self.body_text = "Restaurante Exemplo\nEndereço: Rua Central, 10\nTelefone: +55 51 0000-0000"

    def _complete_search(self):
        self.body_text = "Resultados\nRestaurante Exemplo\nCentro, Porto Alegre"


class FakeBrowserController:
    def __init__(self, page=None, cdp_endpoint="http://127.0.0.1:9222"):
        self.page = page
        self.cdp_endpoint = cdp_endpoint

    def getActivePage(self):
        return self.page


class AivioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        self.controller = FakeBrowserController(self.page)
        self.integration = AivioIntegration(self.controller)

    def test_search_fills_city_clicks_ver_agora_and_returns_results(self):
        result = self.integration.search_city("  Porto Alegre  ")

        self.assertEqual(self.page.city_input.filled, "Porto Alegre")
        self.assertEqual(self.page.city_input.index, 0)
        self.assertTrue(self.page.search_button.clicked)
        self.assertIn("Ver agora", self.page.search_button_name.pattern)
        self.assertEqual(self.page.waited_for_change[1]["timeout"], 20_000)
        self.assertEqual(result, {
            "status": "completed",
            "city": "Porto Alegre",
            "results": [{
                "title": "Restaurante Exemplo",
                "text": "Restaurante Exemplo\nCentro, Porto Alegre",
                "url": "https://app.aivio.example/company/123",
                "elementType": "card",
            }],
            "companyDetails": None,
        })

    def test_open_first_company_reads_its_page_and_links(self):
        result = self.integration.search_city("Porto Alegre", open_first_company=True)

        self.assertEqual(
            self.page.navigated_to,
            ("https://app.aivio.example/company/123", "domcontentloaded"),
        )
        self.assertEqual(result["companyDetails"], {
            "title": "Restaurante Exemplo | AIVIO",
            "url": "https://app.aivio.example/company/123",
            "text": "Restaurante Exemplo\nEndereço: Rua Central, 10\nTelefone: +55 51 0000-0000",
            "links": [{"title": "Site", "url": "https://example.invalid/"}],
        })

    def test_rejects_empty_city_and_non_aivio_page(self):
        with self.assertRaisesRegex(ValueError, "cidade"):
            self.integration.search_city(" ")
        self.page._title = "Company Directory"
        self.page.url = "https://directory.example/search"
        with self.assertRaisesRegex(RuntimeError, "não foi reconhecida como AIVIO"):
            self.integration.search_city("Porto Alegre")

    def test_rejects_missing_page_city_field_or_search_button(self):
        self.controller.page = None
        with self.assertRaisesRegex(RuntimeError, "Nenhuma aba ativa"):
            self.integration.search_city("Porto Alegre")

        self.controller.page = self.page
        self.page.city_input.items = [{"index": 0, "visible": True, "disabled": False, "descriptors": "busca"}]
        with self.assertRaisesRegex(RuntimeError, "Campo de cidade"):
            self.integration.search_city("Porto Alegre")

        self.page.city_input.items = [{
            "index": 0, "visible": True, "disabled": False, "descriptors": "cidade",
        }]
        self.page.search_button.visible = False
        with self.assertRaisesRegex(RuntimeError, 'Ver agora'):
            self.integration.search_city("Porto Alegre")

    def test_company_navigation_is_restricted_to_same_origin(self):
        with self.assertRaisesRegex(ValueError, "mesma origem"):
            self.integration.read_company_page(self.page, "https://outside.example/company")
        with self.assertRaisesRegex(ValueError, "mesma origem"):
            self.integration.read_company_page(self.page, "file:///C:/Users/user/file")
        self.assertIsNone(self.page.navigated_to)

    def test_open_first_company_skips_external_result_links(self):
        self.page.search_results = [
            {"title": "Site externo", "url": "https://company.example/"},
            {"title": "Perfil AIVIO", "url": "https://app.aivio.example/company/123"},
        ]

        result = self.integration.search_city("Porto Alegre", open_first_company=True)

        self.assertEqual(
            self.page.navigated_to,
            ("https://app.aivio.example/company/123", "domcontentloaded"),
        )
        self.assertEqual(result["companyDetails"]["title"], "Restaurante Exemplo | AIVIO")

    def test_requires_the_exact_loopback_cdp_endpoint(self):
        with self.assertRaisesRegex(ValueError, "127.0.0.1:9222"):
            AivioIntegration(FakeBrowserController(self.page, "http://localhost:9222"))

    def test_city_input_metadata_does_not_return_input_values(self):
        self.assertNotIn("element.value", CITY_INPUTS_SCRIPT)
        self.assertIn("descriptors", CITY_INPUTS_SCRIPT)


if __name__ == "__main__":
    unittest.main()
