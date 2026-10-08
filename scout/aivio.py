import argparse
import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Locator, Page

from scout.browser_controller import BrowserController
from scout.lead_normalization import deduplicate_leads, extract_lead, normalize_company_name
from scout.v2_models import V2Lead

CDP_ENDPOINT = "http://127.0.0.1:9222"
logger = logging.getLogger("scout.aivio")
CITY_INPUTS_SCRIPT = """() => Array.from(document.querySelectorAll('input:not([type="hidden"]), textarea, select'))
  .map((element, index) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    const visible = rect.width > 0 && rect.height > 0 &&
      style.visibility !== 'hidden' && style.display !== 'none';
    const labels = Array.from(element.labels || []).map(label => label.innerText || label.textContent || '');
    return {
      index,
      tagName: element.tagName.toLocaleLowerCase(),
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
    const links = Array.from(element.querySelectorAll('a[href]'))
      .filter(link => visible(link))
      .map(link => ({
        title: (link.innerText || link.getAttribute('aria-label') || '').trim(),
        url: link.href
      }));
    return {
      title: ((heading && (heading.innerText || heading.textContent)) ||
        (link && (link.innerText || link.getAttribute('aria-label'))) || text).trim(),
      text,
      url: href,
      links,
      elementType: cards.length ? 'card' : 'link'
    };
  }).filter(Boolean);
}"""
PAGINATION_BUTTONS_SCRIPT = """() => Array.from(document.querySelectorAll('button,[role="button"]'))
  .map((element, index) => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return {
      index,
      visible: rect.width > 0 && rect.height > 0 &&
        style.visibility !== 'hidden' && style.display !== 'none',
      disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true',
      label: (
        element.getAttribute('aria-label') ||
        element.innerText ||
        element.textContent ||
        element.getAttribute('title') ||
        ''
      ).replace(/\\s+/g, ' ').trim()
    };
  })"""
GENERATION_ACTIONS_SCRIPT = """() => Array.from(document.querySelectorAll('button,[role="button"],a[href]'))
  .map(element => {
    const rect = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    const role = element.matches('a[href]') ? 'link' : 'button';
    return {
      role,
      visible: rect.width > 0 && rect.height > 0 &&
        style.visibility !== 'hidden' && style.display !== 'none',
      disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true',
      label: (
        element.getAttribute('aria-label') ||
        element.innerText ||
        element.textContent ||
        element.getAttribute('title') ||
        ''
      ).replace(/\\s+/g, ' ').trim()
    };
  })"""
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
    .map(link => ({
      title: (link.innerText || link.getAttribute('aria-label') || link.getAttribute('title') || '').trim(),
      url: link.href
    }));
}"""


class AivioIntegration:
    def __init__(self, browser_controller: BrowserController, generation_timeout_ms: int = 120_000) -> None:
        if browser_controller.cdp_endpoint != CDP_ENDPOINT:
            raise ValueError(f"A integração AIVIO aceita somente CDP local em {CDP_ENDPOINT}")
        if not 1_000 <= generation_timeout_ms <= 600_000:
            raise ValueError("generation_timeout_ms deve estar entre 1000 e 600000")
        self.browser_controller = browser_controller
        self.generation_timeout_ms = generation_timeout_ms

    def search_city(
        self,
        city: str,
        open_first_company: bool = False,
        *,
        state: str | None = None,
        category: str | None = None,
        quantity: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state is not None or category is not None or filters is not None or quantity != 20:
            if not state or not category:
                raise ValueError("Informe state e category para a pesquisa V2 estruturada")
            response = self.search_leads(city, state, category, quantity, filters)
            response["companyDetails"] = None
            if open_first_company and response["results"]:
                first_lead = V2Lead.from_dict(response["results"][0])
                if first_lead.company_url:
                    page = self._require_aivio_page()
                    response["companyDetails"] = self.read_company_page(page, first_lead.company_url)
            return response

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

    def search_leads(
        self,
        city: str,
        state: str,
        category: str,
        quantity: int = 20,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        search_values = {"city": city, "state": state, "category": category}
        for name, value in search_values.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Informe {name} para pesquisar")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
            raise ValueError("quantity deve ser um inteiro entre 1 e 100")
        filters = filters or {}
        if not isinstance(filters, dict):
            raise ValueError("filters deve ser um objeto")
        if "has_website" in filters and not isinstance(filters["has_website"], bool):
            raise ValueError("filters.has_website deve ser booleano")

        page = self._require_aivio_page()
        self._fill_search_fields(page, search_values)
        raw_results = self._run_search_pages(page, quantity, filters)
        leads = deduplicate_leads([
            extract_lead(item, city=city, state=state, category=category)
            for item in raw_results
        ])
        if "has_website" in filters:
            expected = filters["has_website"]
            leads = [lead for lead in leads if lead.has_website is expected]
        leads = leads[:quantity]
        return {
            "status": "completed" if len(leads) >= quantity else "partial",
            "city": city.strip(),
            "state": state.strip(),
            "category": category.strip(),
            "requested_quantity": quantity,
            "results": [lead.to_dict() for lead in leads],
            "warnings": [] if len(leads) >= quantity else [
                f"Foram encontrados {len(leads)} leads válidos de {quantity} solicitados."
            ],
        }

    def open_company(self, lead: V2Lead) -> V2Lead:
        page = self._require_aivio_page()
        direct_url = lead.company_url or lead.source_url
        if direct_url:
            self._assert_same_origin(page.url, direct_url)
            details = self.read_company_page(page, direct_url)
            return self._merge_company_details(lead, details)

        if not lead.city or not lead.state:
            raise RuntimeError("A busca para localizar a empresa exige city e state no lead")
        search_values = {"city": lead.city, "state": lead.state}
        if lead.category:
            search_values["category"] = lead.category
        self._fill_search_fields(page, search_values)
        raw_results = self._run_search_pages(page, 100, {})
        requested_name = normalize_company_name(lead.company_name)
        match = next(
            (
                result for result in raw_results
                if normalize_company_name(result.get("title")) == requested_name
            ),
            None,
        )
        if match is None or not isinstance(match.get("url"), str):
            raise RuntimeError(f"Empresa não localizada no AIVIO: {lead.company_name}")
        self._assert_same_origin(page.url, match["url"])
        details = self.read_company_page(page, match["url"])
        return self._merge_company_details(lead, details)

    def generate_site(self, lead: V2Lead) -> tuple[V2Lead, list[dict[str, Any]], list[str]]:
        updated_lead = self.open_company(lead)
        page = self._require_aivio_page()
        generation_action = next(
            (
                item for item in page.evaluate(GENERATION_ACTIONS_SCRIPT)
                if isinstance(item, dict)
                and item.get("visible")
                and not item.get("disabled")
                and re.search(r"(?:gerar|criar).{0,30}site|site.{0,30}(?:gerar|criar)", item.get("label", ""), re.I)
            ),
            None,
        )
        if generation_action is None:
            raise RuntimeError("Ação de geração de site não identificada nos controles visíveis do AIVIO")
        action = page.get_by_role(
            generation_action["role"],
            name=re.compile(rf"^\s*{re.escape(generation_action['label'])}\s*$", re.I),
        )
        if not action.count():
            raise RuntimeError("Ação de geração identificada no DOM, mas indisponível para acionamento acessível")
        before_url = page.url
        before_text = self._page_text(page)
        before_links = page.evaluate(DETAIL_LINKS_SCRIPT)
        action.first.click()
        page.wait_for_function(
            """({url, text, links}) => {
              const root = document.querySelector('main,[role="main"]') || document.body;
              const currentText = (root?.innerText || '').trim();
              const currentLinks = Array.from(root?.querySelectorAll('a[href]') || [])
                .map(link => link.href);
              const changed = location.href !== url || currentText !== text ||
                currentLinks.some(link => !links.includes(link));
              const success = /site.{0,30}(gerado|criado|pronto|publicado|conclu[ií]do)|(?:gerado|criado).{0,30}site/i
                .test(currentText);
              const hadSuccess = /site.{0,30}(gerado|criado|pronto|publicado|conclu[ií]do)|(?:gerado|criado).{0,30}site/i
                .test(text);
              const artifactAdded = currentLinks.some(link => {
                if (links.includes(link)) return false;
                const parsed = new URL(link);
                return parsed.pathname.toLowerCase().endsWith('.pdf') ||
                  parsed.origin !== location.origin;
              });
              return changed && ((success && !hadSuccess) || artifactAdded);
            }""",
            arg={
                "url": before_url,
                "text": before_text,
                "links": [link.get("url") for link in before_links if isinstance(link, dict)],
            },
            timeout=self.generation_timeout_ms,
        )
        self._assert_aivio_page(page)
        after_links = page.evaluate(DETAIL_LINKS_SCRIPT)
        old_urls = {
            link.get("url") for link in before_links
            if isinstance(link, dict) and isinstance(link.get("url"), str)
        }
        artifacts = self._artifacts_from_links(after_links, old_urls)
        warnings: list[str] = []
        if not artifacts:
            warnings.append("O AIVIO indicou conclusão, mas nenhum artefato novo foi identificado.")
        return updated_lead, artifacts, warnings

    def browser_health(self) -> dict[str, Any]:
        try:
            self.browser_controller.connect(timeout_ms=1_000)
            browser_status = self.browser_controller.getStatus()
        except Exception as error:
            logger.info("Estado do browser indisponível: %s", error)
            return {
                "browser_connected": False,
                "active_tab": None,
                "aivio_available": False,
                "browser_error": str(error),
            }
        active_tab = browser_status.get("activeTab")
        page = self.browser_controller.getActivePage()
        aivio_available = False
        aivio_error = None
        if page is not None:
            try:
                self._assert_aivio_page(page)
                aivio_available = True
            except (RuntimeError, ValueError) as error:
                aivio_error = str(error)
        return {
            "browser_connected": bool(browser_status.get("connected")),
            "active_tab": active_tab,
            "aivio_available": aivio_available,
            "aivio_error": aivio_error,
        }

    @staticmethod
    def _field_pattern(name: str) -> re.Pattern[str]:
        patterns = {
            "city": r"\b(city|cidade|munic[ií]pio)\b",
            "state": r"\b(state|estado|uf)\b",
            "category": r"\b(category|categoria|segmento|ramo)\b",
        }
        return re.compile(patterns[name], re.I)

    def _fill_search_fields(self, page: Page, fields: dict[str, str]) -> None:
        selector = 'input:not([type="hidden"]), textarea, select'
        locator = page.locator(selector)
        items = locator.evaluate_all(CITY_INPUTS_SCRIPT)
        for field_name, value in fields.items():
            candidate = next(
                (
                    item for item in items
                    if item.get("visible")
                    and not item.get("disabled")
                    and self._field_pattern(field_name).search(item.get("descriptors", ""))
                ),
                None,
            )
            if candidate is None:
                raise RuntimeError(f"Campo de {field_name} não identificado na página AIVIO")
            target = locator.nth(candidate["index"])
            if candidate.get("tagName") == "select":
                target.select_option(label=value)
            else:
                target.fill(value.strip())

    def _run_search_pages(
        self,
        page: Page,
        quantity: int,
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        initial_text = self._page_text(page)
        button = self._search_button(page)
        if button is None:
            raise RuntimeError('Botão "Ver agora" não encontrado na página AIVIO')
        button.click()
        page.wait_for_function(
            "(previousText) => (document.body?.innerText || '').trim() !== previousText",
            arg=initial_text,
            timeout=20_000,
        )
        results: list[dict[str, Any]] = []
        visited_pages = 0
        while visited_pages < 100:
            current = page.evaluate(SEARCH_RESULTS_SCRIPT)
            if not isinstance(current, list):
                raise RuntimeError("A leitura dos resultados AIVIO retornou um formato inválido")
            results.extend(item for item in current if isinstance(item, dict))
            visited_pages += 1
            leads = deduplicate_leads([extract_lead(item) for item in results])
            if "has_website" in filters:
                expected = filters["has_website"]
                leads = [lead for lead in leads if lead.has_website is expected]
            if len(leads) >= quantity:
                break
            next_button = self._next_page_button(page)
            if next_button is None:
                break
            previous_text = self._page_text(page)
            next_button.click()
            page.wait_for_function(
                "(previousText) => (document.body?.innerText || '').trim() !== previousText",
                arg=previous_text,
                timeout=20_000,
            )
        return results

    @staticmethod
    def _next_page_button(page: Page) -> Locator | None:
        buttons = page.evaluate(PAGINATION_BUTTONS_SCRIPT)
        if not isinstance(buttons, list):
            return None
        next_label = next(
            (
                item.get("label", "").strip()
                for item in buttons
                if isinstance(item, dict)
                and item.get("visible")
                and not item.get("disabled")
                and re.search(r"\b(next|pr[oó]xima|avan[cç]ar|mais resultados|ver mais)\b", item.get("label", ""), re.I)
            ),
            None,
        )
        if not next_label:
            return None
        button = page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(next_label)}\s*$", re.I))
        return button.first if button.count() else None

    @staticmethod
    def _merge_company_details(lead: V2Lead, details: dict[str, Any]) -> V2Lead:
        enriched = extract_lead(
            {
                "title": details.get("title"),
                "url": details.get("url"),
                "text": details.get("text"),
                "links": details.get("links", []),
            },
            city=lead.city,
            state=lead.state,
            category=lead.category,
            company_details=details,
        )
        for field_name in (
            "category", "phone", "address", "city", "state", "website", "instagram",
            "source_url", "company_url", "has_website",
        ):
            if getattr(enriched, field_name) is not None:
                setattr(lead, field_name, getattr(enriched, field_name))
        lead.raw_data["company_details"] = details
        return lead

    @staticmethod
    def _artifacts_from_links(
        links: Any,
        previous_urls: set[str],
    ) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        if not isinstance(links, list):
            return artifacts
        for link in links:
            if not isinstance(link, dict):
                continue
            url = link.get("url")
            if not isinstance(url, str) or url in previous_urls:
                continue
            parsed = urlsplit(url)
            title = link.get("title") if isinstance(link.get("title"), str) else ""
            if parsed.path.casefold().endswith(".pdf"):
                artifacts.append({"type": "pdf", "path": None, "url": url})
            elif parsed.scheme in {"http", "https"} and parsed.hostname:
                artifacts.append({"type": "website", "url": url, "title": title})
        return artifacts

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
        current_url = page.url or ""
        hostname = urlsplit(current_url).hostname or ""
        title = page.title() or ""
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
        try:
            current = urlsplit(current_url or "")
            target = urlsplit(target_url or "")
            if (
                not current.scheme or not current.hostname
                or not target.scheme or not target.hostname
                or target.scheme.casefold() not in {"http", "https"}
                or target.username is not None
                or target.password is not None
            ):
                return False
            current_port = current.port or (443 if current.scheme.casefold() == "https" else 80)
            target_port = target.port or (443 if target.scheme.casefold() == "https" else 80)
            return (
                current.scheme.casefold(),
                current.hostname.casefold(),
                current_port,
            ) == (
                target.scheme.casefold(),
                target.hostname.casefold(),
                target_port,
            )
        except ValueError:
            return False


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
