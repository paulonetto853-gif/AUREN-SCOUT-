import unittest
import re

from scout.aivio import (
    AivioIntegration,
    CITY_INPUTS_SCRIPT,
    DETAIL_LINKS_SCRIPT,
    GENERATION_ACTIONS_SCRIPT,
    PAGE_TEXT_SCRIPT,
    PAGINATION_BUTTONS_SCRIPT,
    SEARCH_RESULTS_SCRIPT,
)


class FakeLocator:
    def __init__(self, *, items=None, visible=True, click_action=None):
        self.items = items or []
        self.visible = visible
        self.click_action = click_action
        self.filled = None
        self.clicked = False
        self.fills = {}

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
        self.fills[getattr(self, "index", 0)] = value

    def select_option(self, *, label):
        self.selected_label = label

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
            "tagName": "INPUT",
            "visible": True,
            "disabled": False,
            "descriptors": "cidade",
        }, {
            "index": 1,
            "tagName": "INPUT",
            "visible": True,
            "disabled": False,
            "descriptors": "estado uf",
        }, {
            "index": 2,
            "tagName": "INPUT",
            "visible": True,
            "disabled": False,
            "descriptors": "categoria segmento",
        }])
        self.search_button = FakeLocator(click_action=self._complete_search)
        self.generation_button = FakeLocator(click_action=self._complete_generation)
        self.next_button = FakeLocator(click_action=self._complete_next_page)
        self.pagination_buttons = []
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
        if selector not in {
            'input:not([type="hidden"]), textarea',
            'input:not([type="hidden"]), textarea, select',
        }:
            raise AssertionError(f"unexpected selector: {selector}")
        return self.city_input

    def evaluate(self, script, *args, **kwargs):
        if script == PAGE_TEXT_SCRIPT:
            return self.body_text
        if script == SEARCH_RESULTS_SCRIPT:
            return self.search_results
        if script == DETAIL_LINKS_SCRIPT:
            return list(self.detail_links)
        if script == PAGINATION_BUTTONS_SCRIPT:
            return self.pagination_buttons
        if script == GENERATION_ACTIONS_SCRIPT:
            return [{
                "role": "button",
                "visible": self.generation_button.visible,
                "disabled": False,
                "label": "Gerar site",
            }]
        if script.startswith("() => Array.from(document.querySelectorAll('button"):
            return []
        raise AssertionError(f"unexpected page script: {script}")

    def get_by_role(self, role, name):
        if role not in {"button", "link"}:
            raise AssertionError(f"unexpected role: {role}")
        if re.search(r"next|pr[oó]xima", name.pattern, re.I):
            return self.next_button
        if re.search(r"gerar|criar", name.pattern, re.I):
            self.generation_button_name = name
            return self.generation_button
        self.search_button_name = name
        return self.search_button

    def get_by_text(self, text, exact):
        if (text, exact) != ("Ver agora", True):
            raise AssertionError("unexpected text locator")
        return FakeLocator(visible=False)

    def wait_for_function(self, expression, **kwargs):
        self.waited_for_change = (expression, kwargs)
        if getattr(self, "timeout_wait", False):
            raise TimeoutError("condition timed out")

    def wait_for_load_state(self, state):
        self.waited_for_load = state

    def goto(self, url, wait_until):
        self.navigated_to = (url, wait_until)
        self.url = url
        self._title = "Restaurante Exemplo | AIVIO"
        self.body_text = "Restaurante Exemplo\nEndereço: Rua Central, 10\nTelefone: +55 51 0000-0000"

    def _complete_search(self):
        self.body_text = "Resultados\nRestaurante Exemplo\nCentro, Porto Alegre"

    def _complete_generation(self):
        self.body_text += "\nSite gerado com sucesso"
        self.detail_links.append({"title": "Site gerado", "url": "https://restaurante.aivio.site/"})

    def _complete_next_page(self):
        self.search_results = [{
            "title": "Bistro Exemplo",
            "text": "Bistro Exemplo\nPorto Alegre",
            "url": "https://app.aivio.example/company/456",
        }]
        self.pagination_buttons = []
        self.body_text += "\nBistro Exemplo"


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

    def test_search_leads_applies_all_task_fields_and_normalizes_results(self):
        result = self.integration.search_leads(
            "Porto Alegre", "RS", "restaurantes", quantity=1,
        )
        self.assertEqual(
            self.page.city_input.fills,
            {0: "Porto Alegre", 1: "RS", 2: "restaurantes"},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["results"][0]["company_name"], "Restaurante Exemplo")
        self.assertEqual(result["results"][0]["city"], "Porto Alegre")

    def test_search_city_supports_structured_v2_arguments_without_breaking_legacy(self):
        result = self.integration.search_city(
            "Porto Alegre", state="RS", category="restaurantes", quantity=1,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["requested_quantity"], 1)
        self.assertIsNone(result["companyDetails"])

    def test_search_leads_paginates_until_requested_quantity(self):
        self.page.pagination_buttons = [{
            "index": 0, "visible": True, "disabled": False, "label": "Next",
        }]
        result = self.integration.search_leads("Porto Alegre", "RS", "restaurantes", quantity=2)
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(self.page.next_button.clicked)
        self.assertEqual(result["status"], "completed")

    def test_open_company_updates_lead_with_page_observations(self):
        from scout.v2_models import V2Lead

        lead = V2Lead(
            company_name="Restaurante Exemplo",
            city="Porto Alegre",
            state="RS",
            company_url="https://app.aivio.example/company/123",
        )
        updated = self.integration.open_company(lead)
        self.assertEqual(updated.phone, "+555100000000")
        self.assertEqual(updated.address, "Rua Central, 10")
        self.assertEqual(updated.website, "https://example.invalid/")
        self.assertEqual(updated.company_url, "https://app.aivio.example/company/123")

    def test_generate_site_waits_for_a_success_signal_and_returns_artifact(self):
        from scout.v2_models import V2Lead

        lead = V2Lead(
            company_name="Restaurante Exemplo",
            city="Porto Alegre",
            state="RS",
            company_url="https://app.aivio.example/company/123",
        )
        updated, artifacts, warnings = self.integration.generate_site(lead)
        self.assertEqual(updated.company_name, "Restaurante Exemplo")
        self.assertEqual(artifacts, [{
            "type": "website",
            "url": "https://restaurante.aivio.site/",
            "title": "Site gerado",
        }])
        self.assertEqual(warnings, [])
        self.assertEqual(self.page.waited_for_change[1]["timeout"], 120_000)

    def test_generate_site_propagates_condition_timeout(self):
        from scout.v2_models import V2Lead

        self.page.timeout_wait = True
        lead = V2Lead(
            company_name="Restaurante Exemplo",
            company_url="https://app.aivio.example/company/123",
        )
        with self.assertRaisesRegex(TimeoutError, "condition timed out"):
            self.integration.generate_site(lead)

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
