import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scout.browser_controller import BrowserController


class FakeActionLocator:
    def __init__(
        self,
        page,
        *,
        kind="element",
        visible=True,
        text="",
        value="",
        attributes=None,
        options=None,
        on_click=None,
        tag="button",
        checked=False,
        table_rows=None,
        list_items=None,
    ):
        self.page = page
        self.kind = kind
        self.visible = visible
        self.text = text
        self.value = value
        self.attributes = attributes or {}
        self.options = options or []
        self.on_click = on_click
        self.tag = tag
        self.checked = checked
        self.table_rows = table_rows or []
        self.list_items = list_items or []
        self.selected_text = False
        self.focused = False
        self.enabled = True
        self.wait_visible = visible
        self.click_count = 0

    @property
    def first(self):
        return self

    def count(self):
        return int(self.visible)

    def nth(self, index):
        if index != 0:
            raise AssertionError("unexpected positional locator")
        return self

    def is_visible(self):
        return self.visible

    def wait_for(self, *, state, timeout):
        if state != "visible":
            raise AssertionError("unexpected wait state")
        if not self.wait_visible:
            raise PlaywrightTimeoutError("not visible")

    def click(self, **kwargs):
        self.click_count += 1
        if self.on_click:
            self.on_click()
        else:
            self.page.text += " changed"

    def dblclick(self, **kwargs):
        self.click_count += 2
        self.page.text += " double-clicked"

    def fill(self, value, **kwargs):
        self.value = value

    def focus(self):
        self.page.focused_locator = self

    def input_value(self):
        return self.value

    def get_attribute(self, name):
        return self.attributes.get(name)

    def is_enabled(self):
        return self.enabled

    def is_checked(self):
        return self.checked

    def check(self, **kwargs):
        self.checked = True

    def uncheck(self, **kwargs):
        self.checked = False

    def select_text(self, **kwargs):
        self.selected_text = True
        self.page.focused_locator = self

    def scroll_into_view_if_needed(self):
        self.page.scrolled_to = self

    def set_input_files(self, path, **kwargs):
        self.attributes["uploaded_file"] = path

    def evaluate(self, script, *args):
        if script.startswith("(trigger, args) => {"):
            if args and args[0].get("shouldScroll"):
                if not self.page.dropdown_menu_state.get("scrollable"):
                    state = dict(self.page.dropdown_menu_state)
                    state["options"] = state.get("visible_options", state.get("options", []))
                    state["opened"] = bool(
                        state.get("menu_visible")
                        or state["options"]
                        or state.get("expanded") == "true"
                        or state.get("state") in {"open", "opened", "expanded"}
                    )
                    state["ambiguous"] = False
                    state["scrollable"] = False
                    return state
                result = self.page.dropdown_scroll_results.pop(0)
                self.page.dropdown_menu_state.update(result.get("state", {}))
                if result.get("options") is not None:
                    self.page.dropdown_menu_state["options"] = result["options"]
                if result.get("make_option_visible") and self.page.dropdown_option_locator:
                    self.page.dropdown_option_locator.visible = True
                return {**self.page.dropdown_menu_state, **result["result"]}
            state = dict(self.page.dropdown_menu_state)
            state["options"] = state.get("visible_options", state.get("options", []))
            state.setdefault("evidence", "none")
            state.setdefault("expanded", self.attributes.get("aria-expanded"))
            state.setdefault("state", self.attributes.get("data-state"))
            state.setdefault("trigger_changed", False)
            state["opened"] = bool(
                state.get("menu_visible")
                or state["options"]
                or state["expanded"] == "true"
                or state["state"] in {"open", "opened", "expanded"}
                or state.get("trigger_changed")
            )
            if state["opened"] and state.get("evidence") == "none":
                state["evidence"] = (
                    "visible-options" if state["options"]
                    else "combobox-expanded" if state["expanded"] == "true"
                    else "visible-menu" if state.get("menu_visible")
                    else "combobox-dom-changed"
                )
            return state
        if script.startswith("element => ({") and "childCount:" in script:
            return {
                "expanded": self.attributes.get("aria-expanded"),
                "state": self.attributes.get("data-state"),
                "controls": self.attributes.get("aria-controls"),
                "owns": self.attributes.get("aria-owns"),
                "childCount": self.attributes.get("child_count", 0),
                "text": self.text,
            }
        if "tagName.toLowerCase()" in script:
            return "select" if self.kind == "native" else self.tag
        if "Array.from(element.labels" in script:
            return self.attributes.get("label", "")
        if script.startswith("element => ({") and "expanded:" in script:
            return {
                "text": self.text,
                "expanded": "false" if self.page.dropdown_closed else "true",
                "value": self.value if self.kind == "native" else None,
                "valueText": self.attributes.get("aria-valuetext"),
            }
        if script.startswith("element => ({"):
            return {
                "tag": self.tag,
                "role": self.attributes.get("role", ""),
                "text": self.text,
                "aria_label": self.attributes.get("aria-label", ""),
                "name": self.attributes.get("name", ""),
                "id": self.attributes.get("id", ""),
                "type": self.attributes.get("type", ""),
            }
        if script.startswith("element => ["):
            return " ".join((
                self.text,
                str(self.attributes.get("aria-label", "")),
                str(self.attributes.get("href", "")),
                str(self.attributes.get("name", "")),
                str(self.attributes.get("id", "")),
            ))
        if self.kind == "native":
            if "Array.from(element.options" in script:
                return self.options
            if "element.selectedOptions[0]" in script:
                return self.value
        if "Array.from(table.querySelectorAll" in script:
            return self.table_rows
        if "querySelectorAll(':scope > li" in script:
            return self.list_items
        if self.kind == "custom":
            return {
                "text": self.text,
                "expanded": "false" if self.page.dropdown_closed else "true",
                "value": None,
                "valueText": self.attributes.get("aria-valuetext"),
            }
        raise AssertionError(f"unexpected locator evaluation: {script}")

    def select_option(self, *, value, timeout):
        option = next((item for item in self.options if item["value"] == value), None)
        if option is None:
            raise AssertionError("option should have been validated before selection")
        self.value = option["text"]


class FakeActionPage:
    def __init__(self):
        self.url = "https://aivio.example/dashboard"
        self.text = "Initial page"
        self.roles = {}
        self.texts = {}
        self.labels = {}
        self.placeholders = {}
        self.attributes = {}
        self.dropdown_closed = False
        self.dropdown_menu_state = {
            "opened": False,
            "ambiguous": False,
            "options": [],
            "scrollable": False,
            "scrollTop": None,
            "scrollHeight": None,
            "clientHeight": None,
        }
        self.dropdown_scroll_results = []
        self.dropdown_option_locator = None
        self._title = "AIVIO"
        self._closed = False
        self.focused_locator = None
        self.clipboard = "clipboard text"
        self.keyboard = FakeKeyboard(self)
        self.mouse = FakeMouse(self)
        self.history = [self.url]
        self.history_index = 0
        self.scrolled_to = None

    def is_closed(self):
        return self._closed

    def evaluate(self, script, *args):
        if "document.visibilityState === 'visible'" in script:
            return True
        if "window.location.href" in script and "document.body" in script:
            hash_value = 2166136261
            for character in self.text:
                hash_value ^= ord(character)
                hash_value = (hash_value * 16777619) & 0xFFFFFFFF
            return {"url": self.url, "text_hash": f"{len(self.text)}:{hash_value}"}
        if "document.body && document.body.innerText" in script:
            return self.text
        if "navigator.clipboard.readText()" in script:
            return self.clipboard
        if "navigator.clipboard.writeText(value)" in script:
            self.clipboard = args[0]
            return None
        if "window.scrollTo" in script:
            self.scroll_script = script
            return None
        if "document.documentElement.scrollHeight" in script:
            self.scroll_script = script
            return None
        if script.startswith("() => ({"):
            return {"title": self._title, "url": self.url, "text": self.text}
        raise AssertionError(f"unexpected page evaluation: {script}")

    def wait_for_function(self, expression, *, arg, timeout):
        hash_value = 2166136261
        for character in self.text:
            hash_value ^= ord(character)
            hash_value = (hash_value * 16777619) & 0xFFFFFFFF
        current_hash = f"{len(self.text)}:{hash_value}"
        if arg["url"] == self.url and arg["text_hash"] == current_hash:
            raise PlaywrightTimeoutError("page did not change")

    def wait_for_timeout(self, timeout):
        self.waited_for = timeout

    def get_by_role(self, role, *, name=None, exact=False):
        if name is None:
            matches = [
                locator for (item_role, _), locator in self.roles.items()
                if item_role == role
            ]
            if len(matches) == 1:
                return matches[0]
            return FakeActionLocator(self, visible=False)
        return self.roles.get((role, name), FakeActionLocator(self, visible=False))

    def get_by_text(self, text, *, exact):
        return self.texts.get(text, FakeActionLocator(self, visible=False))

    def get_by_label(self, label, *, exact):
        return self.labels.get(label, FakeActionLocator(self, visible=False))

    def get_by_placeholder(self, placeholder, *, exact):
        return self.placeholders.get(placeholder, FakeActionLocator(self, visible=False))

    def locator(self, selector):
        return self.attributes.get(selector, FakeActionLocator(self, visible=False))

    def title(self):
        return self._title

    def bring_to_front(self):
        self.fronted = True

    def goto(self, url, wait_until, timeout=30_000):
        self.url = url
        self.history = self.history[:self.history_index + 1] + [url]
        self.history_index += 1

    def go_back(self, **kwargs):
        if self.history_index:
            self.history_index -= 1
            self.url = self.history[self.history_index]

    def go_forward(self, **kwargs):
        if self.history_index + 1 < len(self.history):
            self.history_index += 1
            self.url = self.history[self.history_index]

    def reload(self, **kwargs):
        self.reloaded = True

    def wait_for_url(self, expected_url, timeout):
        if self.url != expected_url:
            raise PlaywrightTimeoutError("url mismatch")

    def expect_download(self, timeout):
        page = self

        class DownloadContext:
            value = FakeDownload()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                page.download_waited = timeout

        return DownloadContext()

    def close(self):
        self._closed = True


class FakeDownload:
    suggested_filename = "report.pdf"
    url = "https://aivio.example/report.pdf?access_token=secret#private"

    def failure(self):
        return None

    def path(self):
        return "/tmp/report.pdf"


class FakeKeyboard:
    def __init__(self, page):
        self.page = page
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)
        if key == "Control+C" and self.page.focused_locator is not None:
            self.page.clipboard = self.page.focused_locator.text
        elif key == "Control+V" and self.page.focused_locator is not None:
            self.page.focused_locator.value = self.page.clipboard


class FakeMouse:
    def __init__(self, page):
        self.page = page
        self.wheels = []

    def wheel(self, delta_x, delta_y):
        self.wheels.append((delta_x, delta_y))


class FakeActionContext:
    def __init__(self, page):
        self.pages = [page]

    def new_page(self):
        page = FakeActionPage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, page):
        self.contexts = [FakeActionContext(page)]

    def is_connected(self):
        return True


class BrowserActionTests(unittest.TestCase):
    def setUp(self):
        self.page = FakeActionPage()
        self.controller = BrowserController("http://127.0.0.1:9223")
        self.controller._browser = FakeBrowser(self.page)

    def test_click_button_by_visible_text(self):
        button = FakeActionLocator(self.page, text="Buscar")
        self.page.roles[("button", "Buscar")] = button
        result = self.controller.click_button(text="Buscar")
        self.assertTrue(result["clicked"])
        self.assertTrue(result["page_changed"])

    def test_click_text_uses_an_explicit_exact_target(self):
        target = FakeActionLocator(self.page, text="Detalhes")
        self.page.texts["Detalhes"] = target
        result = self.controller.click_text("Detalhes")
        self.assertTrue(result["clicked"])
        self.assertEqual(target.click_count, 1)

    def test_click_button_by_aria_label(self):
        button = FakeActionLocator(self.page)
        self.page.roles[("button", "Pesquisar")] = button
        result = self.controller.click_button(aria_label="Pesquisar")
        self.assertTrue(result["clicked"])

    def test_button_click_does_not_target_unrelated_text(self):
        unrelated_text = FakeActionLocator(self.page, text="Buscar")
        self.page.texts["Buscar"] = unrelated_text
        with self.assertRaisesRegex(RuntimeError, "não encontrado"):
            self.controller.click_button(text="Buscar")
        self.assertEqual(unrelated_text.click_count, 0)

    def test_button_can_use_stable_data_attributes_as_fallback(self):
        button = FakeActionLocator(self.page, attributes={"data-testid": "save"})
        selector = 'button[data-testid="save"],[role="button"][data-testid="save"]'
        self.page.attributes[selector] = button
        result = self.controller.click_button(data_attributes={"data-testid": "save"})
        self.assertTrue(result["clicked"])

    def test_click_link_by_accessible_text(self):
        link = FakeActionLocator(self.page, text="Ver detalhes")
        self.page.roles[("link", "Ver detalhes")] = link
        result = self.controller.click_link(text="Ver detalhes")
        self.assertTrue(result["clicked"])
        self.assertTrue(result["page_changed"])

    def test_fill_input_by_placeholder(self):
        textbox = FakeActionLocator(self.page, value="")
        self.page.roles[("textbox", "Cidade")] = textbox
        self.page.placeholders["Digite uma cidade"] = textbox
        result = self.controller.fill_input("Porto Alegre", placeholder="Digite uma cidade")
        self.assertEqual(textbox.value, "Porto Alegre")
        self.assertTrue(result["value_changed"])

    def test_fill_input_by_associated_label(self):
        textbox = FakeActionLocator(self.page, value="")
        self.page.roles[("textbox", "Estado")] = textbox
        self.page.labels["Estado"] = textbox
        result = self.controller.fill_input("RS", label="Estado")
        self.assertEqual(textbox.value, "RS")
        self.assertTrue(result["filled"])

    def test_fill_input_refuses_sensitive_fields(self):
        textbox = FakeActionLocator(self.page, attributes={"name": "access_token"})
        self.page.roles[("textbox", "Token")] = textbox
        with self.assertRaisesRegex(ValueError, "sensíveis"):
            self.controller.fill_input("secret", aria_label="Token")

    def test_open_custom_dropdown_and_select_option_by_text(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha uma categoria",
            attributes={"aria-expanded": "false"},
            on_click=lambda: (
                setattr(self.page, "dropdown_closed", False),
                trigger.attributes.update({"aria-expanded": "true"}),
                self.page.dropdown_menu_state.update({
                    "opened": True,
                    "options": ["Restaurantes"],
                }),
            ),
        )
        option = FakeActionLocator(
            self.page,
            text="Restaurantes",
            on_click=lambda: (
                setattr(trigger, "text", "Restaurantes"),
                setattr(self.page, "dropdown_closed", True),
            ),
        )
        self.page.roles[("button", "Categorias")] = trigger
        self.page.roles[("option", "Restaurantes")] = option

        opened = self.controller.open_dropdown("Categorias")
        self.assertTrue(self.controller.dropdown_open(role="button", accessible_name="Categorias"))
        selected = self.controller.select_option("Restaurantes")

        self.assertEqual(opened, {"opened": True, "kind": "custom"})
        self.assertEqual(selected, {"selected": True, "value_changed": True})

    def test_open_aivio_category_combobox_by_stable_data_slot(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Restaurantes, padarias e lanchonetes",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
            on_click=lambda: (
                setattr(self.page, "dropdown_closed", False),
                self.page.dropdown_menu_state.update({
                    "opened": True,
                    "options": ["Barbearias"],
                }),
            ),
        )
        selector = (
            'button[data-slot="select-trigger"],'
            '[role="button"][data-slot="select-trigger"],'
            'select[data-slot="select-trigger"],'
            '[role=\'combobox\'][data-slot="select-trigger"]'
        )
        self.page.attributes[selector] = trigger

        result = self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )

        self.assertEqual(result, {"opened": True, "kind": "custom"})
        self.assertEqual(trigger.click_count, 1)

    def test_dropdown_open_detection_recognizes_visible_options_after_click(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Categoria previamente selecionada",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
            on_click=lambda: self.page.dropdown_menu_state.update({
                "visible_options": ["Barbearias"],
                "evidence": "visible-options",
            }),
        )
        selector = (
            'button[data-slot="select-trigger"],'
            '[role="button"][data-slot="select-trigger"],'
            'select[data-slot="select-trigger"],'
            '[role=\'combobox\'][data-slot="select-trigger"]'
        )
        self.page.attributes[selector] = trigger

        self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )

        self.assertTrue(self.controller.wait_for_dropdown_open(timeout_ms=50))
        self.assertEqual(trigger.click_count, 1)
        self.assertEqual(self.controller.read_dropdown_options(), ["Barbearias"])

    def test_dropdown_open_timeout_reports_dom_state_diagnostic(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
        )
        selector = (
            'button[data-slot="select-trigger"],'
            '[role="button"][data-slot="select-trigger"],'
            'select[data-slot="select-trigger"],'
            '[role=\'combobox\'][data-slot="select-trigger"]'
        )
        self.page.attributes[selector] = trigger
        self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )

        with self.assertRaisesRegex(TimeoutError, "O dropdown não abriu após o clique"):
            self.controller.wait_for_dropdown_open(timeout_ms=1)

        self.assertEqual(trigger.click_count, 1)

    def test_select_dropdown_option_that_is_already_visible(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
            on_click=lambda: setattr(self.page, "dropdown_closed", False),
        )
        option = FakeActionLocator(
            self.page,
            text="Barbearias",
            on_click=lambda: (
                setattr(trigger, "text", "Barbearias"),
                setattr(self.page, "dropdown_closed", True),
            ),
        )
        self.page.attributes['button[data-slot="select-trigger"],[role="button"][data-slot="select-trigger"],select[data-slot="select-trigger"],[role=\'combobox\'][data-slot="select-trigger"]'] = trigger
        self.page.roles[("option", "Barbearias")] = option
        self.page.dropdown_menu_state.update({
            "opened": True,
            "options": ["Barbearias"],
        })

        self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )
        self.assertTrue(self.controller.wait_for_dropdown_open())
        self.assertEqual(self.controller.read_dropdown_options(), ["Barbearias"])
        result = self.controller.select_option("Barbearias")

        self.assertEqual(result, {"selected": True, "value_changed": True})
        self.assertEqual(trigger.text, "Barbearias")

    def test_select_dropdown_option_after_scrolling_its_container(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
            on_click=lambda: setattr(self.page, "dropdown_closed", False),
        )
        option = FakeActionLocator(
            self.page,
            visible=False,
            text="Clínicas e consultórios",
            on_click=lambda: (
                setattr(trigger, "text", "Clínicas e consultórios"),
                setattr(self.page, "dropdown_closed", True),
            ),
        )
        self.page.attributes['button[data-slot="select-trigger"],[role="button"][data-slot="select-trigger"],select[data-slot="select-trigger"],[role=\'combobox\'][data-slot="select-trigger"]'] = trigger
        self.page.roles[("option", "Clínicas e consultórios")] = option
        self.page.dropdown_menu_state.update({
            "opened": True,
            "options": ["Restaurantes", "Barbearias"],
            "scrollable": True,
            "scrollTop": 0,
            "scrollHeight": 600,
            "clientHeight": 200,
        })
        self.page.dropdown_scroll_results.append({
            "result": {
                "scrolled": True,
                "at_end": True,
                "scroll_top": 400,
                "scroll_height": 600,
            },
            "options": ["Barbearias", "Clínicas e consultórios"],
            "make_option_visible": True,
            "state": {"scrollTop": 400},
        })
        self.page.dropdown_option_locator = option

        self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )
        result = self.controller.select_option("Clínicas e consultórios")

        self.assertEqual(result, {"selected": True, "value_changed": True})
        self.assertEqual(option.click_count, 1)
        self.assertEqual(trigger.text, "Clínicas e consultórios")

    def test_missing_dropdown_option_stops_at_end_without_page_scroll(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
            on_click=lambda: setattr(self.page, "dropdown_closed", False),
        )
        self.page.attributes['button[data-slot="select-trigger"],[role="button"][data-slot="select-trigger"],select[data-slot="select-trigger"],[role=\'combobox\'][data-slot="select-trigger"]'] = trigger
        self.page.dropdown_menu_state.update({
            "opened": True,
            "options": ["Restaurantes", "Barbearias"],
            "scrollable": True,
            "scrollTop": 100,
            "scrollHeight": 300,
            "clientHeight": 200,
        })
        self.page.dropdown_scroll_results.append({
            "result": {
                "scrolled": False,
                "at_end": True,
                "scroll_top": 100,
                "scroll_height": 300,
            },
            "state": {"scrollTop": 100},
        })
        self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )

        with self.assertRaisesRegex(RuntimeError, "até o fim do dropdown"):
            self.controller.select_option("Categoria inexistente")

        self.assertFalse(hasattr(self.page, "scroll_script"))

    def test_selection_requires_confirmation_on_trigger_after_scroll(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox", "data-slot": "select-trigger"},
            on_click=lambda: setattr(self.page, "dropdown_closed", False),
        )
        option = FakeActionLocator(self.page, text="Barbearias")
        self.page.attributes['button[data-slot="select-trigger"],[role="button"][data-slot="select-trigger"],select[data-slot="select-trigger"],[role=\'combobox\'][data-slot="select-trigger"]'] = trigger
        self.page.roles[("option", "Barbearias")] = option
        self.page.dropdown_menu_state.update({
            "opened": True,
            "options": ["Barbearias"],
        })
        self.controller.open_dropdown(
            role="combobox",
            data_attributes={"data-slot": "select-trigger"},
        )

        with self.assertRaisesRegex(RuntimeError, "não foi confirmada no controle"):
            self.controller.select_option("Barbearias")

        self.assertEqual(option.click_count, 1)

    def test_clicking_dropdown_text_can_be_confirmed_from_open_control(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox"},
            on_click=lambda: setattr(self.page, "dropdown_closed", False),
        )
        option = FakeActionLocator(
            self.page,
            text="Restaurantes, padarias e lanchonetes",
            on_click=lambda: (
                setattr(trigger, "text", "Restaurantes, padarias e lanchonetes"),
                setattr(self.page, "dropdown_closed", True),
            ),
        )
        self.page.roles[("combobox", "Escolha o ramo")] = trigger
        self.page.roles[("option", "Restaurantes, padarias e lanchonetes")] = option
        self.page.dropdown_menu_state.update({
            "opened": True,
            "options": ["Restaurantes, padarias e lanchonetes"],
        })
        self.page.texts["Restaurantes, padarias e lanchonetes"] = option

        self.controller.open_dropdown("Escolha o ramo")
        self.assertEqual(self.controller.read_dropdown_state()["text"], "Escolha o ramo")
        self.controller.wait_for_element(
            role="option",
            accessible_name="Restaurantes, padarias e lanchonetes",
        )
        self.controller.click_text("Restaurantes, padarias e lanchonetes")
        state = self.controller.read_dropdown_state()

        self.assertEqual(state["text"], "Restaurantes, padarias e lanchonetes")
        self.assertEqual(state["expanded"], "false")

    def test_custom_dropdown_selection_requires_selected_text_on_trigger(self):
        trigger = FakeActionLocator(
            self.page,
            kind="custom",
            text="Escolha o ramo",
            attributes={"role": "combobox", "aria-expanded": "false"},
            on_click=lambda: setattr(self.page, "dropdown_closed", False),
        )
        option = FakeActionLocator(
            self.page,
            text="Restaurantes, padarias e lanchonetes",
            on_click=lambda: setattr(self.page, "dropdown_closed", True),
        )
        self.page.roles[("combobox", "Escolha o ramo")] = trigger
        self.page.roles[("option", "Restaurantes, padarias e lanchonetes")] = option
        self.page.dropdown_menu_state.update({
            "opened": True,
            "options": ["Restaurantes, padarias e lanchonetes"],
        })

        self.controller.open_dropdown("Escolha o ramo")
        with self.assertRaisesRegex(RuntimeError, "não foi confirmada no controle"):
            self.controller.select_option("Restaurantes, padarias e lanchonetes")

        self.assertEqual(trigger.text, "Escolha o ramo")

    def test_select_option_from_native_select(self):
        select = FakeActionLocator(
            self.page,
            kind="native",
            value="",
            options=[
                {"text": "Restaurantes", "value": "restaurants"},
                {"text": "Bar", "value": "bar"},
            ],
        )
        select.select_option = lambda *, value, timeout: setattr(
            select, "value", next(item["text"] for item in select.options if item["value"] == value)
        )
        self.page.roles[("combobox", "Categoria")] = select

        self.controller.open_dropdown("Categoria")
        result = self.controller.select_option("Bar")

        self.assertEqual(result, {"selected": True, "value_changed": True})
        self.assertEqual(select.value, "Bar")

    def test_nonexistent_option_is_detected_before_clicking(self):
        trigger = FakeActionLocator(self.page, kind="custom")
        option = FakeActionLocator(self.page, visible=False)
        self.page.roles[("button", "Categoria")] = trigger
        self.page.roles[("option", "Inexistente")] = option
        self.page.texts["Inexistente"] = FakeActionLocator(self.page, visible=False)
        self.controller.open_dropdown("Categoria")
        self.page.dropdown_menu_state.update({
            "menu_visible": True,
            "options": [],
        })

        with self.assertRaisesRegex(RuntimeError, "container rolável"):
            self.controller.select_option("Inexistente")

    def test_ambiguous_button_is_rejected(self):
        class AmbiguousLocator(FakeActionLocator):
            def count(self):
                return 2

            def nth(self, index):
                return FakeActionLocator(self.page)

        self.page.roles[("button", "Publicar")] = AmbiguousLocator(self.page)
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            self.controller.click_button(text="Publicar")

    def test_page_change_can_be_verified_explicitly(self):
        before = self.controller._snapshot_page(self.page)
        self.page.text += " updated"
        self.assertTrue(self.controller.verify_page_changed(before))

    def test_wait_for_element_times_out_when_target_does_not_appear(self):
        missing = FakeActionLocator(self.page, visible=False)
        missing.wait_visible = False
        self.page.roles[("button", "Continuar")] = missing

        with patch("scout.browser_controller.time.monotonic", side_effect=[0.0, 0.0]):
            with self.assertRaises(PlaywrightTimeoutError):
                self.controller.wait_for_element(
                    role="button",
                    accessible_name="Continuar",
                    timeout_ms=10,
                )

    def test_click_element_and_double_click_are_explicit(self):
        target = FakeActionLocator(self.page, text="Abrir")
        self.page.roles[("button", "Abrir")] = target
        self.assertTrue(self.controller.click_element(
            role="button", accessible_name="Abrir"
        )["clicked"])
        self.assertTrue(self.controller.double_click(
            role="button", accessible_name="Abrir"
        )["clicked"])
        self.assertEqual(target.click_count, 3)

    def test_clear_checkbox_radio_and_native_select_confirm_state(self):
        textbox = FakeActionLocator(self.page, tag="input", value="old")
        self.page.roles[("textbox", "Cidade")] = textbox
        self.page.labels["Cidade"] = textbox
        self.assertTrue(self.controller.element_filled(label="Cidade"))
        self.assertTrue(self.controller.clear_input(label="Cidade")["cleared"])
        self.assertFalse(self.controller.element_filled(label="Cidade"))

        checkbox = FakeActionLocator(
            self.page, tag="input", checked=False, attributes={"type": "checkbox"}
        )
        radio = FakeActionLocator(
            self.page, tag="input", checked=False, attributes={"type": "radio"}
        )
        self.page.roles[("checkbox", "Aceito")] = checkbox
        self.page.roles[("radio", "Opção A")] = radio
        self.page.labels["Aceito"] = checkbox
        self.page.labels["Opção A"] = radio
        self.assertTrue(self.controller.set_checkbox(label="Aceito")["checked"])
        self.assertTrue(self.controller.set_radio(label="Opção A")["selected"])
        self.assertTrue(self.controller.element_selected(
            role="checkbox", accessible_name="Aceito"
        ))

        native = FakeActionLocator(
            self.page,
            kind="native",
            tag="select",
            options=[{"text": "RS", "value": "rs"}],
        )
        self.page.roles[("combobox", "Estado")] = native
        self.page.labels["Estado"] = native
        result = self.controller.select_native_option("RS", label="Estado")
        self.assertTrue(result["selected"])

    def test_keyboard_clipboard_copy_and_paste(self):
        source = FakeActionLocator(self.page, text="copied text")
        destination = FakeActionLocator(self.page, tag="input", value="")
        self.page.texts["source"] = source
        self.page.labels["destination"] = destination
        self.assertEqual(self.controller.copy_text(text="source"), "copied text")
        self.controller.paste_text(label="destination", text="pasted text")
        self.assertEqual(destination.value, "pasted text")
        self.controller.press_key("Enter")
        self.controller.press_key("Escape")
        self.controller.press_key("Tab")
        self.controller.press_key("Backspace")
        self.controller.keyboard_shortcut("Ctrl+A")
        self.assertEqual(
            self.page.keyboard.pressed,
            ["Control+C", "Control+V", "Enter", "Escape", "Tab", "Backspace", "Control+A"],
        )
        with self.assertRaises(ValueError):
            self.controller.press_key("Meta+R")

    def test_navigation_tabs_and_scroll_are_scoped(self):
        self.assertEqual(self.controller.navigate("https://example.com/")["url"], "https://example.com/")
        self.assertEqual(self.controller.back()["url"], "https://aivio.example/dashboard")
        self.assertEqual(self.controller.forward()["url"], "https://example.com/")
        self.controller.reload()
        self.assertTrue(self.page.reloaded)
        self.assertEqual(self.controller.open_new_tab("https://example.org/")["url"], "https://example.org/")
        self.assertEqual(self.controller.switch_tab(url="https://example.com/")["url"],
                         "https://example.com/")
        self.controller.close_tab(title="AIVIO", url="https://example.org/")
        self.assertTrue(self.controller.list_tabs())
        self.controller.scroll_by(0, 400)
        self.assertEqual(self.page.mouse.wheels, [(0, 400)])
        self.controller.scroll_top()
        self.controller.scroll_bottom()
        self.assertIn("scrollTo", self.page.scroll_script)

    def test_wait_read_and_state_primitives(self):
        button = FakeActionLocator(self.page, text="Pronto")
        self.page.roles[("button", "Pronto")] = button
        self.page.texts["Pronto"] = button
        self.assertTrue(self.controller.wait_for_text("Pronto"))
        self.assertTrue(self.controller.find_text("Pronto")["found"])
        self.assertEqual(self.controller.wait_for_url(self.page.url), self.page.url)
        self.assertTrue(self.controller.element_exists(role="button", accessible_name="Pronto"))
        self.assertTrue(self.controller.element_visible(role="button", accessible_name="Pronto"))
        self.assertTrue(self.controller.element_enabled(role="button", accessible_name="Pronto"))
        self.assertTrue(self.controller.find_element(
            role="button", accessible_name="Pronto"
        )["found"])
        self.assertFalse(self.controller.element_exists(
            role="button", accessible_name="Missing"
        ))
        before = self.controller._snapshot_page(self.page)
        self.page.text += " changed"
        self.assertTrue(self.controller.page_changed(before))
        self.assertFalse(self.controller.url_changed(self.page.url))
        self.assertIsNone(self.controller.dropdown_open(role="button", accessible_name="Pronto"))

        table = FakeActionLocator(
            self.page,
            tag="table",
            table_rows=[{"name": "Empresa"}],
        )
        listing = FakeActionLocator(
            self.page,
            tag="ul",
            list_items=["A", "B"],
        )
        self.page.roles[("table", "Leads")] = table
        self.page.roles[("list", "Categorias")] = listing
        table_result = self.controller.read_table(accessible_name="Leads")
        self.assertEqual(table_result["rows"], [{"name": "Empresa"}])
        self.assertEqual(table_result["source_url"], self.page.url)
        list_result = self.controller.read_list(accessible_name="Categorias")
        self.assertEqual(
            [item["text"] for item in list_result["items"]],
            ["A", "B"],
        )
        self.assertEqual(self.controller.read_attributes(["id"], role="table", accessible_name="Leads"),
                         {"id": None})
        with patch.object(self.controller, "inspectPage", return_value={
            "links": [{"text": "Ajuda", "href": "https://example.test/help"}],
            "buttons": [{"text": "Buscar", "type": "button"}],
            "inputs": [{"tag": "input", "type": "text", "value": None}],
        }):
            self.assertEqual(self.controller.read_links()[0]["text"], "Ajuda")
            self.assertEqual(self.controller.read_buttons()[0]["text"], "Buscar")
            self.assertIsNone(self.controller.read_inputs()[0]["value"])

        self.assertTrue(self.controller.scroll_to_element(
            role="button", accessible_name="Pronto"
        ))

    def test_page_change_wait_times_out_explicitly(self):
        before = self.controller._snapshot_page(self.page)
        with self.assertRaises(PlaywrightTimeoutError):
            self.controller.wait_for_page_change(before, timeout_ms=10)

    def test_download_and_explicit_upload(self):
        download_target = FakeActionLocator(self.page, text="Baixar relatório")
        self.page.roles[("button", "Baixar relatório")] = download_target
        result = self.controller.wait_for_download(
            role="button", accessible_name="Baixar relatório"
        )
        self.assertEqual(result["suggested_filename"], "report.pdf")
        self.assertEqual(result["url"], "https://aivio.example/report.pdf")
        self.assertEqual(self.page.download_waited, 30_000)

        with tempfile.NamedTemporaryFile() as uploaded:
            expected_path = str(Path(uploaded.name).resolve())
            file_input = FakeActionLocator(
                self.page,
                tag="input",
                attributes={"type": "file"},
            )
            self.page.attributes['input[name="document"]'] = file_input
            result = self.controller.upload_file(
                uploaded.name,
                tag_name="input",
                name="document",
            )
        self.assertTrue(result["uploaded"])
        self.assertEqual(
            file_input.attributes["uploaded_file"],
            expected_path,
        )


if __name__ == "__main__":
    unittest.main()
