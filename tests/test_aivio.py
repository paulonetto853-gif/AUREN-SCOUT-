import unittest

from scout.aivio import AivioIntegration
from scout.v2_models import V2Lead


class FakePage:
    def __init__(self):
        self.url = "https://app.aivio.example/search"
        self._title = "AIVIO - Pesquisa"

    def title(self):
        return self._title


class FakeBrowserController:
    def __init__(self, *, state_inputs=None, buttons=None, pages=None):
        self.page = FakePage()
        self.cdp_endpoint = "http://127.0.0.1:9223"
        self.state_inputs = state_inputs if state_inputs is not None else [{
            "tag": "input",
            "type": "text",
            "name": "state",
            "labels": ["Estado"],
            "data_attributes": {},
        }]
        self.buttons = buttons if buttons is not None else [
            {"text": "Escolha o ramo", "disabled": False},
            {"text": "Buscar", "disabled": False},
        ]
        self.pages = pages if pages is not None else [[{
            "title": "Restaurante Exemplo",
            "text": "Restaurante Exemplo\nCentro, Porto Alegre\nTelefone: (51) 99999-0000",
            "links": [{
                "text": "Empresa",
                "url": "https://app.aivio.example/company/123",
            }],
            "source_url": self.page.url,
        }]]
        self.page_index = 0
        self.operations = []
        self.text = "Pesquisa de empresas"
        self.links = []
        self.fail_wait = False

    def getActivePage(self):
        return self.page

    def connect(self, **kwargs):
        self.operations.append(("connect", kwargs))

    def active_tab(self):
        return {"title": self.page.title(), "url": self.page.url}

    def read_inputs(self):
        return [
            {"tag": "input", "type": "text", "placeholder": "Digite uma cidade..."},
            *self.state_inputs,
        ]

    def read_buttons(self):
        return list(self.buttons)

    def fill_input(self, value, **target):
        self.operations.append(("fill", value, target))
        return {"filled": True, "value_changed": True}

    def select_native_option(self, value, **target):
        self.operations.append(("select_native", value, target))
        return {"selected": True, "value": value}

    def open_dropdown(self, trigger, **target):
        self.operations.append(("open_dropdown", trigger, target))
        return {"opened": True, "kind": "custom"}

    def select_option(self, value):
        self.operations.append(("select_option", value))
        return {"selected": True, "value_changed": True}

    def capture_page_state(self):
        return {"url": self.page.url, "text_hash": str(self.page_index)}

    def click_button(self, *, text, **target):
        self.operations.append(("click_button", text))
        if text in {"Buscar", "Ver agora"}:
            self.page_index = min(self.page_index + 1, len(self.pages))
            self.text = "Resultados de busca"
        elif text in {"Next", "Próxima"}:
            self.page_index = min(self.page_index + 1, len(self.pages) - 1)
        elif "site" in text.casefold():
            self.text = "Site gerado com sucesso"
            self.links.append({
                "text": "Site gerado",
                "href": "https://restaurante.aivio.site/",
            })
        return {"clicked": True}

    def wait_for_page_change(self, before, timeout_ms=5_000):
        self.operations.append(("wait_for_page_change", timeout_ms))
        if self.fail_wait:
            raise TimeoutError("A página não mudou")
        return True

    def read_semantic_records(self):
        index = min(self.page_index, len(self.pages) - 1)
        return self.pages[index]

    def navigate(self, url):
        self.operations.append(("navigate", url))
        self.page.url = url
        self.page._title = "Restaurante Exemplo | AIVIO"
        self.text = "Restaurante Exemplo\nEndereço: Rua Central, 10\nTelefone: +55 51 0000-0000"
        self.links = [{"text": "Site", "href": "https://example.invalid/"}]
        return {"url": url}

    def read_visible_text(self):
        return self.text

    def read_links(self):
        return list(self.links)


class AivioIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeBrowserController()
        self.integration = AivioIntegration(self.controller)

    def test_search_requires_explicit_credit_authorization(self):
        with self.assertRaisesRegex(PermissionError, "autorização explícita"):
            self.integration.search_leads("Porto Alegre", "RS", "restaurantes")
        self.assertFalse(any(item[0] == "click_button" for item in self.controller.operations))

    def test_search_uses_confirmed_control_names_and_returns_normalized_leads(self):
        result = self.integration.search_leads(
            "Porto Alegre",
            "RS",
            "restaurantes",
            quantity=1,
            allow_credit_consumption=True,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["results"][0]["company_name"], "Restaurante Exemplo")
        self.assertEqual(result["results"][0]["phone"], "51999990000")
        self.assertEqual(result["results"][0]["company_url"], "https://app.aivio.example/company/123")
        self.assertIn(("fill", "Porto Alegre", {"placeholder": "Digite uma cidade..."}),
                      self.controller.operations)
        self.assertIn(("fill", "RS", {"label": "Estado"}), self.controller.operations)
        self.assertIn(("open_dropdown", "Escolha o ramo", {}), self.controller.operations)
        self.assertIn(("select_option", "restaurantes"), self.controller.operations)
        self.assertIn(("click_button", "Buscar"), self.controller.operations)
        self.assertTrue(any(item[0] == "wait_for_page_change" for item in self.controller.operations))

    def test_native_state_select_uses_confirmed_stable_name(self):
        self.controller.state_inputs = [{
            "tag": "select",
            "type": "select",
            "name": "state",
            "labels": [],
            "data_attributes": {},
        }]
        self.integration.search_leads(
            "Porto Alegre", "RS", "restaurantes", quantity=1,
            allow_credit_consumption=True,
        )
        self.assertIn(
            ("select_native", "RS", {"name": "state"}),
            self.controller.operations,
        )

    def test_missing_state_selector_fails_before_search(self):
        self.controller.state_inputs = []
        with self.assertRaisesRegex(RuntimeError, "Estado sem atributo estável confirmado"):
            self.integration.search_leads(
                "Porto Alegre", "RS", "restaurantes", allow_credit_consumption=True,
            )
        self.assertFalse(any(item[0] == "click_button" for item in self.controller.operations))

    def test_ambiguous_state_selector_fails_explicitly(self):
        self.controller.state_inputs = [
            {"name": "state", "labels": [], "data_attributes": {}},
            {"aria_label": "Estado", "labels": [], "data_attributes": {}},
        ]
        with self.assertRaisesRegex(RuntimeError, "Estado ambíguo"):
            self.integration.search_leads(
                "Porto Alegre", "RS", "restaurantes", allow_credit_consumption=True,
            )

    def test_category_and_search_controls_must_be_unique(self):
        self.controller.buttons.append({"text": "Buscar", "disabled": False})
        with self.assertRaisesRegex(RuntimeError, "Buscar.*ambíguo"):
            self.integration.search_leads(
                "Porto Alegre", "RS", "restaurantes", allow_credit_consumption=True,
            )

    def test_missing_category_control_fails_before_form_interaction(self):
        self.controller.buttons = [{"text": "Buscar", "disabled": False}]
        with self.assertRaisesRegex(RuntimeError, 'Botão "Escolha o ramo" não encontrado'):
            self.integration.search_leads(
                "Porto Alegre", "RS", "restaurantes", allow_credit_consumption=True,
            )
        self.assertFalse(any(item[0] == "fill" for item in self.controller.operations))

    def test_repeated_pages_stop_pagination(self):
        self.controller.pages = [[{
            "title": "Restaurante Exemplo",
            "text": "Restaurante Exemplo",
            "links": [],
            "source_url": self.controller.page.url,
        }]]
        self.controller.buttons.append({"text": "Next", "disabled": False})
        result = self.integration.search_leads(
            "Porto Alegre", "RS", "restaurantes", quantity=2,
            allow_credit_consumption=True,
        )
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue(any("repetiu resultados" in warning for warning in result["warnings"]))

    def test_page_change_timeout_is_not_reported_as_success(self):
        self.controller.fail_wait = True
        with self.assertRaisesRegex(TimeoutError, "página não mudou"):
            self.integration.search_leads(
                "Porto Alegre", "RS", "restaurantes", allow_credit_consumption=True,
            )

    def test_open_company_uses_controller_navigation_and_same_origin(self):
        lead = V2Lead(
            company_name="Restaurante Exemplo",
            company_url="https://app.aivio.example/company/123",
        )
        updated = self.integration.open_company(lead)
        self.assertIn(
            ("navigate", "https://app.aivio.example/company/123"),
            self.controller.operations,
        )
        self.assertEqual(updated.address, "Rua Central, 10")
        with self.assertRaisesRegex(ValueError, "mesma origem"):
            self.integration.open_company(V2Lead(
                company_name="Externa",
                company_url="https://outside.example/company/1",
            ))

    def test_open_company_search_requires_credit_authorization(self):
        lead = V2Lead(
            company_name="Restaurante Exemplo",
            city="Porto Alegre",
            state="RS",
            category="restaurantes",
        )
        with self.assertRaisesRegex(PermissionError, "autorização explícita"):
            self.integration.open_company(lead)

    def test_open_company_search_requires_category_before_form_interaction(self):
        lead = V2Lead(company_name="Restaurante Exemplo", city="Porto Alegre", state="RS")
        with self.assertRaisesRegex(RuntimeError, "exige category"):
            self.integration.open_company(lead, allow_credit_consumption=True)
        self.assertFalse(any(item[0] == "fill" for item in self.controller.operations))

    def test_site_generation_requires_both_authorizations_and_observes_artifact(self):
        lead = V2Lead(
            company_name="Restaurante Exemplo",
            company_url="https://app.aivio.example/company/123",
        )
        self.controller.buttons.append({"text": "Gerar site", "disabled": False})
        with self.assertRaises(PermissionError):
            self.integration.generate_site(lead)
        updated, artifacts, warnings = self.integration.generate_site(
            lead,
            allow_credit_consumption=True,
            allow_external_effects=True,
        )
        self.assertTrue(updated.company_name)
        self.assertEqual(artifacts[0]["url"], "https://restaurante.aivio.site/")
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
