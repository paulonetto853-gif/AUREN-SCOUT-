import logging
import os
import re
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

logger = logging.getLogger("scout.browser")

V1_CDP_ENDPOINT = "http://127.0.0.1:9222"
V2_CDP_ENDPOINT = "http://127.0.0.1:9223"
_ALLOWED_CDP_PORTS = {9222, 9223}
INSPECT_PAGE_SCRIPT = """() => {
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const safeValue = element => {
    const descriptor = [
      element.name,
      element.id,
      element.getAttribute('autocomplete'),
      element.getAttribute('aria-label'),
      element.getAttribute('placeholder')
    ].filter(Boolean).join(' ').toLowerCase();
    const sensitive = /password|token|cookie|csrf|auth|secret|credential|session/;
    if (sensitive.test(descriptor)) return null;
    return 'value' in element ? element.value : null;
  };
  const root = document.querySelector('main,[role="main"]') || document.body;
  const text = (root && root.innerText || '').trim();
  const safeHref = href => {
    try {
      const url = new URL(href, document.baseURI);
      url.search = '';
      url.hash = '';
      return url.href;
    } catch {
      return '';
    }
  };
  return {
    title: document.title,
    url: (() => {
      const url = new URL(window.location.href);
      url.search = '';
      url.hash = '';
      return url.href;
    })(),
    text: text.slice(0, 12000),
    inputs: Array.from(document.querySelectorAll('input,textarea,select'))
      .filter(element => visible(element) &&
        !(element instanceof HTMLInputElement && element.type.toLowerCase() === 'password') &&
        !(element instanceof HTMLInputElement && element.type.toLowerCase() === 'hidden'))
      .map(element => ({
        tag: element.tagName.toLowerCase(),
        type: element instanceof HTMLInputElement ? element.type : element.tagName.toLowerCase(),
        name: element.getAttribute('name') || '',
        placeholder: element.getAttribute('placeholder') || '',
        aria_label: element.getAttribute('aria-label') || '',
        value: safeValue(element)
      })),
    buttons: Array.from(document.querySelectorAll('button,[role="button"]'))
      .filter(visible)
      .map(element => ({
        text: (element.innerText || element.textContent || '').trim(),
        aria_label: element.getAttribute('aria-label') || '',
        type: element.getAttribute('type') || ''
      })),
    links: Array.from(document.querySelectorAll('a[href]'))
      .filter(visible)
      .map(element => ({
        text: (element.innerText || element.textContent || '').trim(),
        href: safeHref(element.href)
      }))
  };
}"""
INSPECT_CATEGORY_DOM_SCRIPT = """() => {
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const sensitiveName = /password|token|cookie|csrf|auth|secret|credential|session/i;
  const safeText = value => (value || '').replace(
    /Bearer\\s+[a-z0-9._~+/=-]+|\\beyJ[a-zA-Z0-9_-]{10,}\\.[a-zA-Z0-9_-]{10,}\\.[a-zA-Z0-9_-]{10,}\\b/gi,
    '[REDACTED]'
  ).slice(0, 180);
  const safeAttributes = element => {
    const data = {};
    const aria = {};
    for (const attribute of element.attributes) {
      const name = attribute.name.toLowerCase();
      if (sensitiveName.test(name)) continue;
      const value = safeText(attribute.value);
      if (name.startsWith('data-')) data[name] = value;
      if (name.startsWith('aria-')) aria[name] = value;
    }
    return {data_attributes: data, aria_attributes: aria};
  };
  const inputInfo = element => {
    if (element instanceof HTMLInputElement &&
        ['password', 'hidden'].includes(element.type.toLowerCase())) return null;
    const labels = Array.from(element.labels || [])
      .map(label => safeText(label.innerText || label.textContent || '').trim())
      .filter(Boolean);
    return {
      tag: element.tagName.toLowerCase(),
      type: element instanceof HTMLInputElement ? element.type : element.tagName.toLowerCase(),
      name: element.getAttribute('name') || '',
      id: element.id || '',
      placeholder: element.getAttribute('placeholder') || '',
      aria_label: element.getAttribute('aria-label') || '',
      role: element.getAttribute('role') || '',
      autocomplete: element.getAttribute('autocomplete') || '',
      labels,
      class: element.className && typeof element.className === 'string' ? element.className : '',
      ...safeAttributes(element)
    };
  };
  const elements = Array.from(document.body.querySelectorAll('*')).filter(element => {
    if (!visible(element)) return false;
    if (element instanceof HTMLInputElement && ['password', 'hidden'].includes(element.type.toLowerCase())) {
      return false;
    }
    const text = (element.innerText || element.textContent || '').trim();
    const hasRelevantAttribute = Array.from(element.attributes).some(attribute => {
      const name = attribute.name.toLowerCase();
      return name === 'role' || name.startsWith('aria-') || name.startsWith('data-');
    });
    const semanticTag = /^(button|option|li|select)$/.test(element.tagName.toLowerCase());
    const compactText = text.length > 0 && text.length <= 120 &&
      element.querySelectorAll('*').length <= 4;
    return hasRelevantAttribute || semanticTag || compactText;
  }).slice(0, 800);
  const records = elements.map((element, index) => {
    const text = safeText(element.innerText || element.textContent || '').trim();
    const attributes = safeAttributes(element);
    const signature = JSON.stringify([
      element.tagName.toLowerCase(),
      element.getAttribute('role') || '',
      element.id || '',
      element.getAttribute('aria-label') || '',
      text,
      attributes.data_attributes
    ]);
    return {
      index,
      signature,
      tag: element.tagName.toLowerCase(),
      text,
      role: element.getAttribute('role') || '',
      aria_label: element.getAttribute('aria-label') || '',
      class: element.className && typeof element.className === 'string' ? element.className : '',
      ...attributes
    };
  });
  const textInputs = Array.from(document.querySelectorAll('input[type="text"]'))
    .filter(element => visible(element))
    .map(inputInfo)
    .filter(Boolean);
  const categoryButton = records.find(item =>
    item.tag === 'button' && item.text.toLocaleLowerCase() === 'escolha o ramo'
  ) || null;
  return {
    title: document.title,
    url: (() => {
      const url = new URL(window.location.href);
      url.search = '';
      url.hash = '';
      return url.href;
    })(),
    inputs: Array.from(document.querySelectorAll('input,textarea,select'))
      .filter(element => visible(element))
      .map(inputInfo)
      .filter(Boolean),
    second_text_input_without_placeholder:
      textInputs[1] && !textInputs[1].placeholder ? textInputs[1] : null,
    category_button: categoryButton,
    elements: records
  };
}"""

_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
)
_SENSITIVE_FIELD_PATTERN = re.compile(
    r"password|token|cookie|csrf|auth|secret|credential|session",
    re.IGNORECASE,
)


def _validate_http_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and parts.port != 0
        )
    except ValueError as error:
        raise ValueError("URL inválida; use uma URL HTTP(S) sem credenciais") from error
    if not valid:
        raise ValueError("URL inválida; use uma URL HTTP(S) sem credenciais")


class BrowserController:
    def __init__(self, cdp_endpoint: str | None = None) -> None:
        self.cdp_endpoint = (
            cdp_endpoint
            if cdp_endpoint is not None
            else os.getenv("SCOUT_CDP_ENDPOINT", V1_CDP_ENDPOINT)
        )
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def connect(self, timeout_ms: int = 5_000) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms < 1:
            raise ValueError("timeout_ms deve ser um inteiro positivo")
        if self._playwright is not None:
            stale_playwright = self._playwright
            self._browser = None
            self._playwright = None
            stale_playwright.stop()
        if not self.cdp_endpoint:
            raise RuntimeError("Configure SCOUT_CDP_ENDPOINT com o endpoint CDP do Edge")
        try:
            parts = urlsplit(self.cdp_endpoint)
            valid_endpoint = (
                parts.scheme == "http"
                and parts.hostname == "127.0.0.1"
                and parts.username is None
                and parts.password is None
                and not parts.query
                and not parts.fragment
                and parts.port in _ALLOWED_CDP_PORTS
            )
        except ValueError as error:
            raise ValueError(
                "SCOUT_CDP_ENDPOINT deve ser http://127.0.0.1:9222 (V1) "
                "ou http://127.0.0.1:9223 (V2)"
            ) from error
        if not valid_endpoint:
            raise ValueError(
                "SCOUT_CDP_ENDPOINT deve ser http://127.0.0.1:9222 (V1) "
                "ou http://127.0.0.1:9223 (V2)"
            )

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.connect_over_cdp(self.cdp_endpoint, timeout=timeout_ms)
            session = browser.new_browser_cdp_session()
            try:
                product = session.send("Browser.getVersion")["product"]
            finally:
                session.detach()
            if "edg/" not in product.casefold() and "microsoft edge" not in product.casefold():
                raise RuntimeError(f"O endpoint CDP não pertence ao Microsoft Edge ({product})")
        except Exception:
            playwright.stop()
            raise

        self._playwright = playwright
        self._browser = browser

    def disconnect(self) -> None:
        self._browser = None
        playwright = self._playwright
        self._playwright = None
        if playwright is not None:
            playwright.stop()

    def _require_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            raise RuntimeError("Browser Controller não está conectado ao Edge")
        return self._browser

    def _pages(self) -> list[Page]:
        browser = self._require_browser()
        return [page for context in browser.contexts for page in context.pages if not page.is_closed()]

    @staticmethod
    def _tab_info(page: Page) -> dict[str, str]:
        return {"title": page.title(), "url": page.url}

    def listTabs(self) -> list[dict[str, str]]:
        return [self._tab_info(page) for page in self._pages()]

    def getActiveTab(self) -> dict[str, str] | None:
        page = self.getActivePage()
        return self._tab_info(page) if page is not None else None

    def getActivePage(self) -> Page | None:
        for page in self._pages():
            if page.evaluate("document.visibilityState === 'visible' && document.hasFocus()"):
                return page
        return None

    def getStatus(self) -> dict:
        browser = self._require_browser()
        tabs = self.listTabs()
        active_tab = self.getActiveTab()
        return {
            "connected": browser.is_connected(),
            "browser": "Microsoft Edge",
            "tabs": tabs,
            "activeTab": active_tab,
        }

    def navigate(self, url: str) -> dict[str, str]:
        _validate_http_url(url)
        logger.info("Navegação solicitada para host=%s", urlsplit(url).hostname)
        active_page = next(
            (page for page in self._pages()
             if page.evaluate("document.visibilityState === 'visible' && document.hasFocus()")),
            None,
        )
        if active_page is None:
            raise RuntimeError("Não há uma aba ativa disponível para navegar")
        active_page.goto(url, wait_until="domcontentloaded")
        return self._tab_info(active_page)

    def readPageInfo(self) -> dict[str, str] | None:
        page = self._active_page()
        return self._tab_info(page) if page is not None else None

    def inspectPage(self) -> dict:
        browser = self._require_browser()
        page = self.getActivePage()
        if page is None:
            raise RuntimeError("Não há uma aba ativa disponível para inspecionar")
        inspection = page.evaluate(INSPECT_PAGE_SCRIPT)
        if not isinstance(inspection, dict):
            raise RuntimeError("A inspeção da página retornou um formato inválido")
        url = inspection.get("url")
        if isinstance(url, str):
            parts = urlsplit(url)
            inspection["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        text = inspection.get("text")
        if isinstance(text, str):
            for pattern in _SENSITIVE_TEXT_PATTERNS:
                text = pattern.sub("[REDACTED]", text)
            inspection["text"] = text[:12_000]
        inputs = inspection.get("inputs")
        if isinstance(inputs, list):
            safe_inputs = []
            for item in inputs:
                if not isinstance(item, dict):
                    continue
                field_type = item.get("type", "")
                if isinstance(field_type, str) and field_type.casefold() in {"password", "hidden"}:
                    continue
                descriptor = " ".join(
                    str(item.get(key, "")) for key in ("name", "placeholder", "aria_label")
                )
                safe_value = None if _SENSITIVE_FIELD_PATTERN.search(descriptor) else item.get("value")
                safe_inputs.append({
                    "tag": item.get("tag", ""),
                    "type": field_type,
                    "name": item.get("name", ""),
                    "placeholder": item.get("placeholder", ""),
                    "aria_label": item.get("aria_label", ""),
                    "value": safe_value,
                })
            inspection["inputs"] = safe_inputs
        inspection["connected"] = browser.is_connected()
        return inspection

    def inspectCategoryDropdown(self) -> dict:
        browser = self._require_browser()
        page = self.getActivePage()
        if page is None:
            raise RuntimeError("Não há uma aba ativa disponível para inspecionar")
        hostname = urlsplit(page.url or "").hostname or ""
        if "aivio" not in hostname.casefold() and not re.search(r"\baivio\b", page.title(), re.I):
            raise RuntimeError("A aba ativa não foi reconhecida como AIVIO")

        before = page.evaluate(INSPECT_CATEGORY_DOM_SCRIPT)
        if not isinstance(before, dict):
            raise RuntimeError("A inspeção inicial do dropdown retornou um formato inválido")
        category_button = page.get_by_role(
            "button",
            name=re.compile(r"^\s*Escolha o ramo\s*$", re.I),
        )
        button_count = category_button.count()
        if button_count != 1:
            raise RuntimeError(
                f"Era esperado exatamente um botão 'Escolha o ramo'; encontrados: {button_count}"
            )

        category_button.click()
        page.wait_for_timeout(300)
        after = page.evaluate(INSPECT_CATEGORY_DOM_SCRIPT)
        if not isinstance(after, dict):
            raise RuntimeError("A inspeção do dropdown aberto retornou um formato inválido")

        before_counts: dict[str, int] = {}
        for item in before.get("elements", []):
            if isinstance(item, dict) and isinstance(item.get("signature"), str):
                signature = item["signature"]
                before_counts[signature] = before_counts.get(signature, 0) + 1

        new_elements = []
        after_counts: dict[str, int] = {}
        for item in after.get("elements", []):
            if not isinstance(item, dict) or not isinstance(item.get("signature"), str):
                continue
            signature = item["signature"]
            after_counts[signature] = after_counts.get(signature, 0) + 1
            if after_counts[signature] > before_counts.get(signature, 0):
                new_elements.append({key: value for key, value in item.items() if key != "signature"})

        after_url = after.get("url")
        if isinstance(after_url, str):
            parts = urlsplit(after_url)
            after["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        before_category_button = before.get("category_button")
        if isinstance(before_category_button, dict):
            before_category_button = {
                key: value for key, value in before_category_button.items() if key != "signature"
            }

        return {
            "connected": browser.is_connected(),
            "title": after.get("title", ""),
            "url": after.get("url", ""),
            "inputs_before_open": before.get("inputs", []),
            "second_text_input_without_placeholder": before.get(
                "second_text_input_without_placeholder"
            ),
            "category_button": before_category_button,
            "dropdown_opened": True,
            "new_elements": new_elements[:200],
            "element_limit_reached": len(new_elements) > 200,
        }

    def screenshot(self) -> bytes | None:
        page = self._active_page()
        return page.screenshot() if page is not None else None

    def _active_page(self) -> Page | None:
        return self.getActivePage()
