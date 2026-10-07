import logging
import os
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

logger = logging.getLogger("scout.browser")


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
        self.cdp_endpoint = cdp_endpoint if cdp_endpoint is not None else os.getenv("SCOUT_CDP_ENDPOINT", "")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    def connect(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        if not self.cdp_endpoint:
            raise RuntimeError("Configure SCOUT_CDP_ENDPOINT com o endpoint CDP do Edge")
        try:
            parts = urlsplit(self.cdp_endpoint)
            valid_endpoint = (
                parts.scheme in {"http", "https"}
                and bool(parts.hostname)
                and parts.username is None
                and parts.password is None
                and not parts.query
                and not parts.fragment
                and parts.port != 0
            )
        except ValueError as error:
            raise ValueError("SCOUT_CDP_ENDPOINT deve ser uma URL HTTP(S) válida, sem credenciais") from error
        if not valid_endpoint:
            raise ValueError("SCOUT_CDP_ENDPOINT deve ser uma URL HTTP(S) válida, sem credenciais")

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.connect_over_cdp(self.cdp_endpoint)
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

    def screenshot(self) -> bytes | None:
        page = self._active_page()
        return page.screenshot() if page is not None else None

    def _active_page(self) -> Page | None:
        return self.getActivePage()
