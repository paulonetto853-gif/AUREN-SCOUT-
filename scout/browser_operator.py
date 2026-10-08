from __future__ import annotations

import logging
import os
import re
import threading
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from scout.browser_controller import (
    BrowserController,
    V2_CDP_ENDPOINT,
)

logger = logging.getLogger("scout.browser_operator")

_TARGET_FIELDS = {
    "role",
    "accessible_name",
    "text",
    "aria_label",
    "label",
    "placeholder",
    "name",
    "element_id",
    "data_attributes",
    "tag_name",
}
_ACTION_FIELDS = {
    "navigate": {"url", "timeout_ms"},
    "observe": set(),
    "click": {"target", "timeout_ms"},
    "fill": {"target", "value", "timeout_ms"},
    "select": {"target", "option", "timeout_ms"},
    "press": {"key"},
    "scroll": {"target", "delta_x", "delta_y"},
    "wait": {"target", "text", "url", "timeout_ms"},
    "read_text": {"target", "max_chars"},
    "open_tab": {"url", "timeout_ms"},
    "switch_tab": {"title", "url"},
    "back": {"timeout_ms"},
    "forward": {"timeout_ms"},
    "reload": {"timeout_ms"},
    "verify": {"target", "text", "url", "state", "timeout_ms"},
}
_SAFE_ERROR_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b"),
    re.compile(
        r"""(?i)(?:access[_ -]?token|refresh[_ -]?token|csrf(?:[_ -]?token)?|"""
        r"""authorization|cookie|password|secret|api[_ -]?key)\s*[:=]\s*"""
        r"""["']?[^\s,;"'}&#]+"""
    ),
)
_SAFE_KEYS = {
    "Enter",
    "Escape",
    "Tab",
    "Backspace",
    "Delete",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "Space",
}
_PROHIBITED_TARGET_PATTERN = re.compile(
    r"payment|pay\b|checkout|billing|transaction|pagamento|pagar|cobran[çc]a|"
    r"purchase|whats\s*app|whatsapp|wa\.me|\bmessage\b|\bmensagem\b|\bsend\b|\benviar\b|\bchat\b",
    re.IGNORECASE,
)


class V2BrowserController(BrowserController):
    """V2-only entry point with a V2-only CDP endpoint."""

    def __init__(self, cdp_endpoint: str | None = None) -> None:
        endpoint = (
            cdp_endpoint
            if cdp_endpoint is not None
            else os.getenv("SCOUT_CDP_ENDPOINT", V2_CDP_ENDPOINT)
        )
        if endpoint != V2_CDP_ENDPOINT:
            raise ValueError(f"O operador V2 aceita somente {V2_CDP_ENDPOINT}")
        super().__init__(cdp_endpoint=endpoint)

    def navigate(self, url: str, timeout_ms: int = 30_000) -> dict[str, str]:
        self._assert_safe_destination(url)
        return super().navigate(url, timeout_ms)

    def open_new_tab(self, url: str, timeout_ms: int = 30_000) -> dict[str, str]:
        self._assert_safe_destination(url)
        return super().open_new_tab(url, timeout_ms)

    @staticmethod
    def _assert_safe_destination(url: str) -> None:
        parts = urlsplit(url)
        descriptor = f"{parts.hostname or ''} {parts.path}"
        if _PROHIBITED_TARGET_PATTERN.search(descriptor):
            raise ValueError("Navegação para pagamentos ou serviços de comunicação bloqueada")

    def _guard_target_action(self, locator: Any) -> None:
        page = self.getActivePage()
        if page is None:
            raise RuntimeError("Não há uma aba ativa disponível")
        self._assert_safe_page_context(page)
        super()._guard_target_action(locator)
        descriptor = locator.evaluate(
            """element => [
              element.innerText || element.textContent || '',
              element.getAttribute('aria-label') || '',
              element.getAttribute('placeholder') || '',
              element.getAttribute('name') || '',
              element.getAttribute('href') || '',
              Array.from(element.labels || [])
                .map(label => label.innerText || label.textContent || '').join(' ')
            ].join(' ')"""
        )
        if _PROHIBITED_TARGET_PATTERN.search(descriptor):
            raise ValueError("Interações com pagamentos e mensagens são bloqueadas")

    def _click_explicit_target(self, locator: Any, timeout_ms: int) -> dict[str, bool]:
        self._guard_target_action(locator)
        return super()._click_explicit_target(locator, timeout_ms)

    def press_operator_key(self, key: str) -> None:
        if key not in _SAFE_KEYS:
            raise ValueError("Tecla não permitida pelo operador")
        page = self.getActivePage()
        if page is None:
            raise RuntimeError("Não há uma aba ativa disponível")
        self._assert_safe_page_context(page)
        focused = page.locator(":focus")
        if focused.count():
            self._guard_target_action(focused)
            if key == "Enter":
                focused_kind = focused.evaluate(
                    """element => ({
                      tag: element.tagName.toLowerCase(),
                      type: (element.getAttribute('type') || '').toLowerCase()
                    })"""
                )
                if (
                    focused_kind == {"tag": "button", "type": "submit"}
                    or focused_kind == {"tag": "input", "type": "submit"}
                ):
                    raise ValueError(
                        "Enter em controles de submissão é bloqueado; use um clique explícito"
                    )
        page.keyboard.press(key)

    @staticmethod
    def _assert_safe_page_context(page: Any) -> None:
        if _PROHIBITED_TARGET_PATTERN.search(f"{page.url} {page.title()}"):
            raise ValueError("Interações bloqueadas nesta página por segurança")

    def scroll_container(self, *, delta_y: int, **target: Any) -> dict[str, int | bool]:
        if isinstance(delta_y, bool) or not isinstance(delta_y, int):
            raise ValueError("delta_y deve ser um inteiro")
        locator = self._find_target(**target)
        state = locator.evaluate(
            """(element, delta) => {
              const canScroll = node => {
                const style = window.getComputedStyle(node);
                return node.scrollHeight > node.clientHeight + 1 &&
                  /auto|scroll|overlay/.test(style.overflowY);
              };
              let container = canScroll(element) ? element : element.parentElement;
              while (container && container !== document.body &&
                     container !== document.documentElement && !canScroll(container)) {
                container = container.parentElement;
              }
              if (!container || container === document.body ||
                  container === document.documentElement) {
                return {scrolled: false, scroll_top: 0, scroll_height: 0};
              }
              const before = container.scrollTop;
              container.scrollTop += delta;
              return {
                scrolled: container.scrollTop !== before,
                scroll_top: container.scrollTop,
                scroll_height: container.scrollHeight
              };
            }""",
            delta_y,
        )
        if not isinstance(state, dict) or not all(
            key in state for key in ("scrolled", "scroll_top", "scroll_height")
        ):
            raise RuntimeError("A rolagem do container retornou um formato inválido")
        return state

    def scroll_page(self, *, delta_x: int, delta_y: int) -> dict[str, int | bool]:
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (delta_x, delta_y)):
            raise ValueError("delta_x e delta_y devem ser inteiros")
        page = self.getActivePage()
        if page is None:
            raise RuntimeError("Não há uma aba ativa disponível")
        state = page.evaluate(
            """({deltaX, deltaY}) => {
              const beforeX = window.scrollX;
              const beforeY = window.scrollY;
              window.scrollBy(deltaX, deltaY);
              return {
                scrolled: window.scrollX !== beforeX || window.scrollY !== beforeY,
                scroll_x: window.scrollX,
                scroll_y: window.scrollY
              };
            }""",
            {"deltaX": delta_x, "deltaY": delta_y},
        )
        if not isinstance(state, dict) or not all(
            key in state for key in ("scrolled", "scroll_x", "scroll_y")
        ):
            raise RuntimeError("A rolagem da página retornou um formato inválido")
        return state


class BrowserOperator:
    """Execute an explicit, bounded sequence of browser actions supplied by AUREN."""

    def __init__(self, controller: V2BrowserController | None = None) -> None:
        self.controller = controller or V2BrowserController()

    @staticmethod
    def validate_task(task: Any) -> dict[str, Any]:
        if not isinstance(task, dict):
            raise ValueError("task deve ser um objeto JSON")
        unsupported = set(task) - {"task_id", "actions"}
        if unsupported:
            raise ValueError(f"task contém campos não suportados: {', '.join(sorted(unsupported))}")
        task_id = task.get("task_id")
        if not isinstance(task_id, str):
            raise ValueError("task_id deve ser um UUID válido")
        try:
            task_id = str(uuid.UUID(task_id))
        except ValueError as error:
            raise ValueError("task_id deve ser um UUID válido") from error
        actions = task.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions deve ser uma lista não vazia")
        if len(actions) > 50:
            raise ValueError("actions aceita no máximo 50 operações")
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ValueError(f"actions[{index}] deve ser um objeto")
            action_name = action.get("action")
            if not isinstance(action_name, str) or action_name not in _ACTION_FIELDS:
                raise ValueError(f"actions[{index}].action não é suportada")
            unsupported = set(action) - _ACTION_FIELDS[action_name] - {"action"}
            if unsupported:
                raise ValueError(
                    f"actions[{index}] contém campos não suportados: {', '.join(sorted(unsupported))}"
                )
            target = action.get("target")
            if target is not None:
                BrowserOperator._validate_target(target, index)
            if action_name in {"click", "fill", "select"} and target is None:
                raise ValueError(f"actions[{index}].target é obrigatório")
            if action_name == "fill" and not isinstance(action.get("value"), str):
                raise ValueError(f"actions[{index}].value deve ser texto")
            if action_name == "select" and not _nonempty_text(action.get("option")):
                raise ValueError(f"actions[{index}].option deve ser texto não vazio")
            if action_name in {"navigate", "open_tab"} and not _nonempty_text(action.get("url")):
                raise ValueError(f"actions[{index}].url deve ser texto não vazio")
            if action_name == "switch_tab":
                if (action.get("title") is None) == (action.get("url") is None):
                    raise ValueError(f"actions[{index}] requer exatamente title ou url")
            if action_name == "wait":
                conditions = sum(action.get(key) is not None for key in ("target", "text", "url"))
                if conditions != 1:
                    raise ValueError(f"actions[{index}] requer exatamente target, text ou url")
            if action_name == "verify":
                conditions = sum(
                    action.get(key) is not None
                    for key in ("target", "text", "url")
                )
                if conditions > 1 or (conditions == 0 and action.get("state") != "page_changed"):
                    raise ValueError(
                        f"actions[{index}] requer exatamente target, text, url ou state"
                    )
                if conditions == 1 and action.get("state") == "page_changed":
                    raise ValueError(
                        f"actions[{index}].state page_changed requer verificação sem target/text/url"
                    )
                if action.get("state") not in {
                    None,
                    "page_changed",
                    "visible",
                    "enabled",
                    "selected",
                    "filled",
                }:
                    raise ValueError(f"actions[{index}].state não é suportado")
            if action_name == "press" and action.get("key") not in _SAFE_KEYS:
                raise ValueError(f"actions[{index}].key não é permitida")
        return {"task_id": task_id, "actions": actions}

    @staticmethod
    def _validate_target(target: Any, index: int) -> None:
        if not isinstance(target, dict) or not target:
            raise ValueError(f"actions[{index}].target deve ser um objeto não vazio")
        unknown = set(target) - _TARGET_FIELDS
        if unknown:
            raise ValueError(
                f"actions[{index}].target contém critérios não suportados: "
                f"{', '.join(sorted(unknown))}"
            )
        for name, value in target.items():
            if name == "data_attributes":
                if not isinstance(value, dict) or not value or any(
                    not isinstance(key, str)
                    or not key.startswith("data-")
                    or not isinstance(item, str)
                    for key, item in value.items()
                ):
                    raise ValueError(
                        f"actions[{index}].target.data_attributes deve conter data-* de texto"
                    )
            elif not _nonempty_text(value):
                raise ValueError(f"actions[{index}].target.{name} deve ser texto não vazio")

    def execute(self, task: Any) -> dict[str, Any]:
        try:
            validated = self.validate_task(task)
        except ValueError as error:
            task_id = None
            if isinstance(task, dict) and isinstance(task.get("task_id"), str):
                try:
                    task_id = str(uuid.UUID(task["task_id"]))
                except ValueError:
                    pass
            return {
                "task_id": task_id,
                "status": "failed",
                "action": None,
                "result": None,
                "evidence": [],
                "error": {"code": "invalid_task", "message": str(error)},
            }

        records: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        failed: dict[str, Any] | None = None
        current_action: dict[str, Any] | None = None
        transition_baseline: dict[str, Any] | None = None
        try:
            self.controller.connect()
            for index, action in enumerate(validated["actions"]):
                current_action = {"index": index, "name": action["action"]}
                before = self._observe_state()
                result = self._run_action(action, before, transition_baseline)
                after = self._observe_state()
                record = {
                    "index": index,
                    "action": action["action"],
                    "status": "completed",
                    "result": self._sanitize_result(result),
                    "evidence": {
                        "before": self._public_observation(before),
                        "after": self._public_observation(after),
                    },
                }
                if isinstance(action.get("target"), dict):
                    record["target"] = self._safe_target(action["target"])
                records.append(record)
                evidence.append(record["evidence"])
                if action["action"] not in {"observe", "read_text", "wait", "verify"}:
                    transition_baseline = before
                elif action["action"] == "verify":
                    transition_baseline = None
        except Exception as error:
            code = "operation_timeout" if isinstance(
                error, (PlaywrightTimeoutError, TimeoutError)
            ) else "operation_failed"
            failed = {"code": code, "message": self._safe_error(error, action if "action" in locals() else {})}
            logger.error(
                "Operação web V2 falhou task_id=%s action=%s error=%s",
                validated["task_id"],
                current_action["name"] if current_action else None,
                failed["message"],
            )
            if current_action is not None:
                records.append({
                    "index": current_action["index"],
                    "action": current_action["name"],
                    "status": "failed",
                    "error": failed,
                })
        finally:
            try:
                self.controller.disconnect()
            except Exception as error:
                cleanup_error = self._safe_error(error, {})
                logger.error(
                    "Falha ao desconectar operador web V2 task_id=%s error=%s",
                    validated["task_id"],
                    cleanup_error,
                )
                if failed is None:
                    failed = {"code": "disconnect_failed", "message": cleanup_error}
                else:
                    failed["cleanup_error"] = cleanup_error

        status = "failed" if not records or (failed and not any(
            record["status"] == "completed" for record in records
        )) else "partial" if failed else "completed"
        output: dict[str, Any] = {
            "task_id": validated["task_id"],
            "status": status,
            "action": current_action,
            "result": [record.get("result") for record in records if record["status"] == "completed"],
            "evidence": evidence,
            "operations": records,
        }
        if failed is not None:
            output["error"] = failed
        return output

    def _observe_state(self) -> dict[str, Any]:
        tab = self.controller.getActiveTab()
        if tab is None:
            raise RuntimeError("Não há uma aba ativa disponível para observar")
        return {
            "title": tab["title"],
            "url": tab["url"],
            "state": self.controller.capture_page_state(),
        }

    def _public_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        state = dict(observation["state"])
        state_url = state.get("url")
        if isinstance(state_url, str):
            state["url"] = self.controller._safe_page_url(state_url)
        return {
            "title": observation["title"],
            "url": observation["url"],
            "state": state,
        }

    def _run_action(
        self,
        action: dict[str, Any],
        before: dict[str, Any],
        transition_baseline: dict[str, Any] | None,
    ) -> Any:
        name = action["action"]
        timeout = action.get("timeout_ms", 5_000)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ValueError("timeout_ms deve ser um inteiro positivo")
        target = action.get("target")
        if name == "navigate":
            return self.controller.navigate(action["url"], timeout_ms=timeout)
        if name == "observe":
            return self.controller.inspectPage()
        if name == "click":
            self._require_target(target)
            self.controller.wait_for_element(timeout_ms=timeout, **target)
            found = self.controller.find_element(**target)
            if not found["found"]:
                raise RuntimeError("Elemento não encontrado para clique")
            return self.controller.click_element(timeout_ms=timeout, **target)
        if name == "fill":
            self._require_target(target)
            self.controller.wait_for_element(timeout_ms=timeout, **target)
            found = self.controller.find_element(**target)
            if not found["found"]:
                raise RuntimeError("Campo não encontrado para preenchimento")
            return self.controller.fill_input(action["value"], timeout_ms=timeout, **target)
        if name == "select":
            self._require_target(target)
            self._assert_action_text_allowed(action["option"])
            self.controller.wait_for_element(timeout_ms=timeout, **target)
            found = self.controller.find_element(**target)
            if not found["found"]:
                raise RuntimeError("Dropdown não encontrado")
            if found["element"]["tag"] == "select":
                return self.controller.select_native_option(
                    action["option"], timeout_ms=timeout, **target
                )
            dropdown_target = self._dropdown_target(target)
            self.controller.open_dropdown(timeout_ms=timeout, **dropdown_target)
            return self.controller.select_option(action["option"], timeout_ms=timeout)
        if name == "press":
            key = action.get("key")
            if not isinstance(key, str):
                raise ValueError("key deve ser texto")
            self.controller.press_operator_key(key)
            return {"pressed": key}
        if name == "scroll":
            target = target or {}
            delta_x = action.get("delta_x", 0)
            delta_y = action.get("delta_y", 0)
            for value in (delta_x, delta_y):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("delta_x e delta_y devem ser inteiros")
            if target:
                if delta_x:
                    raise ValueError("rolagem de container aceita somente delta_y")
                return self.controller.scroll_container(delta_y=delta_y, **target)
            return self.controller.scroll_page(delta_x=delta_x, delta_y=delta_y)
        if name == "wait":
            if target is not None:
                if not self.controller.wait_for_element(timeout_ms=timeout, **target):
                    raise RuntimeError("Elemento não apareceu")
                return {"waited_for": "element"}
            if "text" in action:
                self.controller.wait_for_text(action["text"], timeout_ms=timeout)
                return {"waited_for": "text"}
            return {
                "url": self.controller._safe_page_url(
                    self.controller.wait_for_url(action["url"], timeout_ms=timeout)
                )
            }
        if name == "read_text":
            if target is None:
                return {"text": self.controller.read_visible_text(action.get("max_chars", 12_000))}
            found = self.controller.find_element(**target)
            if not found["found"]:
                raise RuntimeError("Elemento não encontrado para leitura")
            return {"text": found["element"]["text"]}
        if name == "open_tab":
            return self.controller.open_new_tab(action["url"], timeout_ms=timeout)
        if name == "switch_tab":
            return self.controller.switch_tab(title=action.get("title"), url=action.get("url"))
        if name in {"back", "forward", "reload"}:
            return getattr(self.controller, name)(timeout_ms=timeout)
        if name == "verify":
            return self._verify(
                action,
                transition_baseline or before,
                timeout,
            )
        raise ValueError(f"Ação não suportada: {name}")

    def _verify(self, action: dict[str, Any], before: dict[str, Any], timeout: int) -> dict[str, Any]:
        if "target" in action:
            target = action["target"]
            found = self.controller.find_element(**target)
            expected_state = action.get("state", "visible")
            if not found["found"]:
                raise RuntimeError("Elemento não encontrado durante a verificação")
            state_checks: dict[str, Callable[..., bool]] = {
                "visible": self.controller.element_visible,
                "enabled": self.controller.element_enabled,
                "selected": self.controller.element_selected,
                "filled": self.controller.element_filled,
            }
            if expected_state not in state_checks:
                raise ValueError("state deve ser visible, enabled, selected ou filled")
            verified = state_checks[expected_state](**target)
            if not verified:
                raise RuntimeError(f"Verificação do elemento falhou: {expected_state}")
            return {"verified": True, "state": expected_state, "element": found["element"]}
        if "text" in action:
            self.controller.wait_for_text(action["text"], timeout_ms=timeout)
            return {"verified": True, "text": action["text"]}
        if "url" in action:
            url = self.controller.wait_for_url(action["url"], timeout_ms=timeout)
            return {"verified": True, "url": self.controller._safe_page_url(url)}
        if action.get("state") == "page_changed":
            changed = self.controller.verify_page_changed(before["state"], timeout)
            if not changed:
                raise PlaywrightTimeoutError(f"A página não mudou em {timeout} ms")
            return {"verified": True, "state": "page_changed"}
        raise ValueError("state deve ser page_changed quando target, text e url não forem informados")

    @staticmethod
    def _require_target(target: Any) -> None:
        if not isinstance(target, dict) or not target:
            raise ValueError("target deve ser um objeto não vazio")

    @staticmethod
    def _dropdown_target(target: dict[str, Any]) -> dict[str, Any]:
        dropdown_target = dict(target)
        trigger = dropdown_target.pop("accessible_name", None)
        text = dropdown_target.pop("text", None)
        if trigger is not None and text is not None:
            raise ValueError("dropdown target não pode combinar accessible_name e text")
        accessible_name = trigger if trigger is not None else text
        if accessible_name is not None:
            dropdown_target["trigger"] = accessible_name
        return dropdown_target

    @staticmethod
    def _sanitize_result(result: Any) -> Any:
        return BrowserController._sanitize_records(result)

    @staticmethod
    def _safe_error(error: Exception, action: dict[str, Any]) -> str:
        message = f"{type(error).__name__}: {error}"
        for field in ("value", "option"):
            value = action.get(field)
            if isinstance(value, str) and value:
                message = message.replace(value, "[REDACTED]")
        for pattern in _SAFE_ERROR_PATTERNS:
            message = pattern.sub("[REDACTED]", message)
        return message

    @staticmethod
    def _assert_action_text_allowed(text: str) -> None:
        if _PROHIBITED_TARGET_PATTERN.search(text):
            raise ValueError("Interações com pagamentos e mensagens são bloqueadas")

    @staticmethod
    def _safe_target(target: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in target.items():
            if isinstance(value, str) and re.search(
                r"password|token|cookie|csrf|auth|secret|credential|session",
                value,
                re.IGNORECASE,
            ):
                safe[key] = "[REDACTED]"
            elif key == "data_attributes" and isinstance(value, dict):
                safe[key] = {
                    name: "[REDACTED]"
                    if re.search(
                        r"password|token|cookie|csrf|auth|secret|credential|session",
                        name,
                        re.IGNORECASE,
                    ) or re.search(
                        r"password|token|cookie|csrf|auth|secret|credential|session",
                        item,
                        re.IGNORECASE,
                    )
                    else item
                    for name, item in value.items()
                }
            else:
                safe[key] = value
        return safe


class BrowserOperatorExecutor:
    def __init__(
        self,
        controller_factory: Callable[[], V2BrowserController] = V2BrowserController,
    ) -> None:
        self._controller_factory = controller_factory
        self._lock = threading.RLock()
        self._results: dict[str, dict[str, Any]] = {}
        self._execution_lock = threading.Lock()

    def execute(self, task: Any) -> dict[str, Any]:
        try:
            validated = BrowserOperator.validate_task(task)
        except ValueError as error:
            return {
                "task_id": None,
                "status": "failed",
                "action": None,
                "result": None,
                "evidence": [],
                "error": {"code": "invalid_task", "message": str(error)},
            }
        task_id = validated["task_id"]
        with self._execution_lock:
            with self._lock:
                if task_id in self._results:
                    return {
                        "task_id": task_id,
                        "status": "failed",
                        "action": None,
                        "result": None,
                        "evidence": [],
                        "error": {
                            "code": "duplicate_task",
                            "message": "task_id já foi executado",
                        },
                    }
            result = BrowserOperator(self._controller_factory()).execute(validated)
            with self._lock:
                self._results[task_id] = result
        return result

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            result = self._results.get(task_id)
            return dict(result) if result is not None else None


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
