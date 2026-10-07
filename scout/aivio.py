import argparse
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Locator, Page

from scout.browser_controller import BrowserController

CDP_ENDPOINT = "http://127.0.0.1:9222"
logger = logging.getLogger("scout.aivio")
CITY_INPUTS_SCRIPT = """() => Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea'))
  .map((element, index) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    const visible = rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
    const labels = Array.from(element.labels || []).map(label => label.innerText || label.textContent || '');
    return {
      index,
      visible,
      disabled: element.disabled,
      descriptors: [
        element.getAttribute('aria-label'),
        element.getAttribute('placeholder'),
        element.getAttribute('name'),
        element.id,
        ...labels
      ].filter(Boolean).join(' ').toLocaleLowerCase()
    };
  })"""
SEARCH_RESULTS_SCRIPT = """() => {
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
  };
  const root = document.querySelector('main,[role="main"]') || document.body;
  const cardSelector = 'article,[role="article"],li,[data-testid*="card" i],[class*="card" i],[class*="result" i],[class*="empresa" i],[class*="company" i]';
  const cards = Array.from(root.querySelectorAll(cardSelector)).filter(visible);
  const candidates = cards.length ? cards : Array.from(root.querySelectorAll('a[href]'))
    .filter(link => visible(link) && !link.closest('nav,header,footer,[role="navigation"]'));
  const seen = new Set();
  return candidates.map(element => {
    const link = element.matches('a[href]') ? element : element.querySelector('a[href]');
    const text = (element.innerText || element.textContent || '').trim();
    if (!text) return null;
    const heading = element.querySelector('h1,h2,h3,[role="heading"]');
    const href = link ? link.href : null;
    const key = href || text;
    if (seen.has(key)) return null;
    seen.add(key);
    return {
      title: ((heading && (heading.innerText || heading.textContent)) ||
        (link && (link.innerText || link.getAttribute('aria-label'))) || text).trim(),
      text,
      url: href,
      elementType: cards.length ? 'card' : 'link'
    };
  }).filter(Boolean);
}"""
PAGE_TEXT_SCRIPT = """() => {
  const root = document.querySelector('main,[role="main"]') || document.body;
  return (root && root.innerText || '').trim();
}"""
DETAIL_LINKS_SCRIPT = """() => {
  const root = document.querySelector('main,[role="main"]') || document.body;
  return Array.from(root.querySelectorAll('a[href]'))
    .filter(link => {
      const rect = link.getBoundingClientRect();
      const style = window.getComputedStyle(link);
      return rect.width > 0 && rect.height > 0 &&
        style.visibility !== 'hidden' && style.display !== 'none';
    })
    .map(link => ({title: (link.innerText || link.getAttribute('aria-label') || '').trim(), url: link.href}))
    .filter(link => link.title);
}"""


class AivioIntegration:
    def __init__(self, browser_controller: BrowserController) -> None:
        if browser_controller.cdp_endpoint != CDP_ENDPOINT:
            raise ValueError(f"A integração AIVIO aceita somente CDP local em {CDP_ENDPOINT}")
        self.browser_controller = browser_controller

    def search_city(self, city: str, open_first_company: bool = False) -> dict[str, Any]:
        city = city.strip()
        if not city:
            raise ValueError("Informe uma cidade para pesquisar")

        page = self._require_aivio_page()
        initial_text = self._page_text(page)
        input_data = page.locator('input:not([type="hidden"]), textarea').evaluate_all(CITY_INPUTS_SCRIPT)
        city_input = next(
            (
                item for item in input_data
                if item["visible"]
                and not item["disabled"]
                and re.search(r"\b(city|cidade|munic[ií]pio)\b", item["descriptors"])
            ),
            None,
        )
        if city_input is None:
            raise RuntimeError("Campo de cidade não identificado na página AIVIO")

        page.locator('input:not([type="hidden"]), textarea').nth(city_input["index"]).fill(city)
        search_button = self._search_button(page)
        if search_button is None:
            raise RuntimeError('Botão "Ver agora" não encontrado na página AIVIO')
        search_button.click()
        logger.info("Pesquisa AIVIO solicitada para cidade=%s", city)
        page.wait_for_function(
            "(previousText) => (document.body?.innerText || '').trim() !== previousText",
            arg=initial_text,
            timeout=20_000,
        )

        results = page.evaluate(SEARCH_RESULTS_SCRIPT)
        response: dict[str, Any] = {
            "status": "completed",
            "city": city,
            "results": results,
            "companyDetails": None,
        }
        if open_first_company:
            company = next(
                (
                    result for result in results
                    if result.get("url") and self._is_same_origin(page.url, result["url"])
                ),
                None,
            )
            if company is None:
                raise RuntimeError("Nenhum link de empresa foi identificado nos resultados AIVIO")
            response["companyDetails"] = self.read_company_page(page, company["url"])
        return response

    def read_company_page(self, page: Page, company_url: str) -> dict[str, Any]:
        self._assert_same_origin(page.url, company_url)
        page.goto(company_url, wait_until="domcontentloaded")
        page.wait_for_load_state("domcontentloaded")
        details = {
            "title": page.title(),
            "url": page.url,
            "text": self._page_text(page),
            "links": page.evaluate(DETAIL_LINKS_SCRIPT),
        }
        self._assert_aivio_page(page)
        return details

    def _require_aivio_page(self) -> Page:
        page = self.browser_controller.getActivePage()
        if page is None:
            raise RuntimeError("Nenhuma aba ativa disponível; abra e autentique o AIVIO manualmente")
        self._assert_aivio_page(page)
        return page

    @staticmethod
    def _assert_aivio_page(page: Page) -> None:
        hostname = urlsplit(page.url).hostname or ""
        title = page.title()
        if "aivio" not in hostname.casefold() and not re.search(r"\baivio\b", title, re.I):
            raise RuntimeError("A aba ativa não foi reconhecida como AIVIO; abra a página manualmente")

    @staticmethod
    def _page_text(page: Page) -> str:
        return page.evaluate(PAGE_TEXT_SCRIPT)

    @staticmethod
    def _search_button(page: Page) -> Locator | None:
        button = page.get_by_role("button", name=re.compile(r"^\s*Ver agora\s*$", re.I))
        if button.count():
            return button.first
        text = page.get_by_text("Ver agora", exact=True)
        return text.first if text.count() else None

    @staticmethod
    def _assert_same_origin(current_url: str, target_url: str) -> None:
        if not AivioIntegration._is_same_origin(current_url, target_url):
            raise ValueError("A abertura de perfil aceita somente links HTTP(S) da mesma origem AIVIO")

    @staticmethod
    def _is_same_origin(current_url: str, target_url: str) -> bool:
        current = urlsplit(current_url)
        target = urlsplit(target_url)
        return not (
            target.scheme not in {"http", "https"}
            or target.username is not None
            or target.password is not None
            or (current.scheme, current.hostname, current.port)
            != (target.scheme, target.hostname, target.port)
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scout.aivio")
    parser.add_argument("city", help="cidade a pesquisar na página AIVIO já aberta")
    parser.add_argument(
        "--open-first-company",
        action="store_true",
        help="abrir o primeiro perfil vinculado após ler os resultados",
    )
    args = parser.parse_args()

    controller = BrowserController(cdp_endpoint=CDP_ENDPOINT)
    try:
        controller.connect()
        result = AivioIntegration(controller).search_city(args.city, args.open_first_company)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()
