import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from scout.aivio import CDP_ENDPOINT
from scout.lead_normalization import (
    deduplicate_leads,
    extract_lead,
    normalize_company_name,
    normalize_phone,
    normalize_text,
    normalize_url,
)
from scout.task_executor import TaskExecutor, _browser_execution_lock
from scout.v2_models import SearchLeadsPayload, Task, TaskStatus, TaskType, V2Lead


def task_request(task_type, payload=None, authorization=None):
    request = {
        "task_id": str(uuid.uuid4()),
        "type": task_type,
        "payload": payload or {},
    }
    if authorization is not None:
        request["authorization"] = authorization
    return request


class FakeIntegration:
    def __init__(self):
        self.health = {
            "browser_connected": True,
            "active_tab": {"title": "AIVIO", "url": "https://app.aivio.example"},
            "aivio_available": True,
        }
        self.generate_error = None
        self.search_authorization = None
        self.open_authorization = None
        self.generate_authorization = None

    def search_leads(self, **kwargs):
        self.search_authorization = kwargs.get("allow_credit_consumption")
        return {
            "status": "completed",
            "city": kwargs["city"],
            "state": kwargs["state"],
            "category": kwargs["category"],
            "requested_quantity": kwargs["quantity"],
            "results": [V2Lead(company_name="Restaurante Exemplo", city=kwargs["city"],
                               state=kwargs["state"], category=kwargs["category"]).to_dict()],
            "warnings": [],
        }

    def open_company(self, lead, **kwargs):
        self.open_authorization = kwargs.get("allow_credit_consumption")
        lead.phone = "5511999999999"
        return lead

    def generate_site(self, lead, **kwargs):
        self.generate_authorization = kwargs
        if self.generate_error:
            raise self.generate_error
        return lead, [{"type": "website", "url": "https://cliente.aivio.example", "title": "Restaurante"}], []

    def browser_health(self):
        return self.health


class TaskContractTests(unittest.TestCase):
    def test_search_payload_preserves_city_value(self):
        for city in ("Porto Alegre", "Canoas", "Caxias do Sul"):
            with self.subTest(city=city):
                task = Task.from_dict(task_request(
                    "SEARCH_LEADS",
                    {"city": city, "state": "RS", "category": "restaurantes"},
                    authorization={"allow_credit_consumption": True},
                ))
                self.assertEqual(task.payload.city, city)

    def test_search_requires_boss_supplied_city_and_category(self):
        base = {"city": "Porto Alegre", "state": "RS", "category": "restaurantes"}
        authorization = {"allow_credit_consumption": True}
        for missing in ("city", "category"):
            with self.subTest(missing=missing):
                payload = {key: value for key, value in base.items() if key != missing}
                with self.assertRaisesRegex(ValueError, f"payload\\.{missing}"):
                    Task.from_dict(task_request(
                        "SEARCH_LEADS",
                        payload,
                        authorization=authorization,
                    ))

    def test_search_task_validates_required_payload_and_uuid(self):
        task = Task.from_dict(task_request("SEARCH_LEADS", {
            "city": "Porto Alegre", "state": "RS", "category": "restaurantes",
            "quantity": 20, "filters": {"has_website": False},
        }, authorization={"allow_credit_consumption": True}))
        self.assertEqual(task.type, TaskType.SEARCH_LEADS)
        self.assertIsInstance(task.payload, SearchLeadsPayload)
        with self.assertRaisesRegex(ValueError, "UUID"):
            Task.from_dict({"task_id": "not-a-uuid", "type": "HEALTH_CHECK", "payload": {}})
        with self.assertRaisesRegex(ValueError, "payload.state"):
            Task.from_dict(task_request("SEARCH_LEADS", {
                "city": "Porto Alegre", "category": "restaurantes",
            }))
        with self.assertRaisesRegex(ValueError, "allow_credit_consumption"):
            Task.from_dict(task_request("SEARCH_LEADS", {
                "city": "Porto Alegre", "state": "RS", "category": "restaurantes",
            }))

    def test_lead_round_trip_has_standard_fields(self):
        lead = V2Lead(company_name="Restaurante Exemplo", city="Porto Alegre")
        encoded = lead.to_dict()
        decoded = V2Lead.from_dict(encoded)
        self.assertEqual(decoded.to_dict(), encoded)
        self.assertIsNone(encoded["has_website"])
        self.assertEqual(encoded["source"], "AIVIO")


class LeadNormalizationTests(unittest.TestCase):
    def test_normalizes_text_phone_url_and_company_name(self):
        self.assertEqual(normalize_text("  Rua  Central\n  10 "), "Rua Central 10")
        self.assertEqual(normalize_phone("+55 (51) 99999-0000"), "+5551999990000")
        self.assertEqual(normalize_url("  example.com/path/  "), "https://example.com/path")
        self.assertIsNone(normalize_url("javascript:alert(1)"))
        self.assertEqual(normalize_company_name("  Clínica São José! "), "clinica sao jose")

    def test_extracts_observed_lead_fields_and_preserves_raw_input(self):
        raw = {
            "title": "Restaurante Exemplo",
            "url": "https://app.aivio.example/company/1",
            "text": "Telefone: +55 (51) 99999-0000\nEndereço: Rua Central, 10",
            "links": [
                {"title": "Site", "url": "example.com"},
                {"title": "Instagram", "url": "https://instagram.com/exemplo"},
            ],
        }
        lead = extract_lead(raw, city="Porto Alegre", state="RS", category="restaurantes")
        self.assertEqual(lead.company_name, "Restaurante Exemplo")
        self.assertEqual(lead.phone, "+5551999990000")
        self.assertEqual(lead.address, "Rua Central, 10")
        self.assertEqual(lead.website, "https://example.com/")
        self.assertEqual(lead.instagram, "https://instagram.com/exemplo")
        self.assertTrue(lead.has_website)
        self.assertEqual(lead.raw_data["search_result"], raw)

    def test_urls_and_raw_data_redact_sensitive_parameters_and_tokens(self):
        url = normalize_url(
            "https://app.aivio.example/company/1?access_token=secret&view=leads#private"
        )
        self.assertEqual(url, "https://app.aivio.example/company/1?view=leads")

        lead = extract_lead({
            "title": "Empresa Exemplo",
            "url": "https://app.aivio.example/company/2?session=private",
            "text": "Bearer abc.def.ghi",
            "links": [],
            "metadata": {"access_token": "raw-secret"},
        })
        self.assertEqual(lead.source_url, "https://app.aivio.example/company/2")
        self.assertNotIn("private", str(lead.raw_data))
        self.assertNotIn("raw-secret", str(lead.raw_data))
        self.assertNotIn("Bearer", str(lead.raw_data))

    def test_deduplication_uses_phone_domain_name_and_company_url(self):
        first = V2Lead(company_name="Loja A", city="Porto Alegre", phone="5511999999999")
        same_phone = V2Lead(company_name="Loja B", city="São Paulo", phone="5511999999999")
        same_domain = V2Lead(company_name="Loja C", website="https://www.exemplo.com")
        same_domain_alt_url = V2Lead(company_name="Loja D", website="https://exemplo.com/")
        same_name_city = V2Lead(company_name="Clínica São José", city="Porto Alegre")
        same_name_city_duplicate = V2Lead(company_name="Clinica Sao Jose", city="Porto Alegre")
        same_company_url = V2Lead(company_name="Outra razão social", company_url="https://app.aivio.example/1")
        same_company_url_duplicate = V2Lead(company_name="Outra empresa", company_url="https://app.aivio.example/1")
        unique_similar = V2Lead(company_name="Clínica São Jose", city="Canoas")

        result = deduplicate_leads([
            first, same_phone, same_domain, same_domain_alt_url,
            same_name_city, same_name_city_duplicate,
            same_company_url, same_company_url_duplicate, unique_similar,
        ])
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0].company_name, "Loja A")
        self.assertTrue(result[0].raw_data["duplicates"])


class TaskExecutorTests(unittest.TestCase):
    def setUp(self):
        self.integration = FakeIntegration()
        self.executor = TaskExecutor(integration=self.integration)

    def test_executes_search_and_returns_standardized_result(self):
        request = task_request("SEARCH_LEADS", {
            "city": "Porto Alegre", "state": "RS", "category": "restaurantes",
        }, authorization={"allow_credit_consumption": True})
        result = self.executor.execute(request).to_dict()
        self.assertEqual(result["task_id"], request["task_id"])
        self.assertEqual(result["type"], "SEARCH_LEADS")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["leads"][0]["company_name"], "Restaurante Exemplo")
        self.assertTrue(result["started_at"])
        self.assertTrue(result["finished_at"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["result"]["leads"][0]["company_name"], "Restaurante Exemplo")
        self.assertIsNone(result["error"])
        self.assertTrue(self.integration.search_authorization)

    def test_v2_defaults_to_its_own_cdp_endpoint(self):
        with patch.dict("os.environ", {}, clear=True):
            executor = TaskExecutor()

        self.assertEqual(CDP_ENDPOINT, "http://127.0.0.1:9223")
        self.assertEqual(executor.browser_controller.cdp_endpoint, CDP_ENDPOINT)

    def test_v2_rejects_the_v1_cdp_endpoint(self):
        with patch.dict("os.environ", {"SCOUT_CDP_ENDPOINT": "http://127.0.0.1:9222"}):
            with self.assertRaisesRegex(ValueError, "127.0.0.1:9223"):
                TaskExecutor()

    def test_executes_open_company_and_generate_site(self):
        lead = V2Lead(company_name="Restaurante Exemplo").to_dict()
        opened = self.executor.execute(task_request(
            "OPEN_COMPANY", {"lead": lead},
            authorization={"allow_credit_consumption": True},
        ))
        generated = self.executor.execute(task_request(
            "GENERATE_SITE", {"lead": lead},
            authorization={
                "allow_credit_consumption": True,
                "allow_external_effects": True,
            },
        ))
        self.assertEqual(opened.status, TaskStatus.COMPLETED)
        self.assertEqual(opened.leads[0].phone, "5511999999999")
        self.assertEqual(generated.artifacts[0]["type"], "website")
        self.assertEqual(generated.status, TaskStatus.COMPLETED)
        self.assertEqual(generated.leads[0].website, "https://cliente.aivio.example")
        self.assertTrue(self.integration.open_authorization)
        self.assertTrue(self.integration.generate_authorization["allow_credit_consumption"])
        self.assertTrue(self.integration.generate_authorization["allow_external_effects"])

    def test_generation_failures_are_explicit(self):
        self.integration.generate_error = TimeoutError(
            "generation timed out for https://aivio.example/?access_token=secret-value"
        )
        lead = V2Lead(company_name="Restaurante Exemplo").to_dict()
        result = self.executor.execute(task_request(
            "GENERATE_SITE", {"lead": lead},
            authorization={
                "allow_credit_consumption": True,
                "allow_external_effects": True,
            },
        ))
        self.assertEqual(result.status, TaskStatus.TIMEOUT)
        self.assertIn("generation timed out", result.errors[0])
        self.assertEqual(result.error["code"], "task_timeout")
        self.assertNotIn("secret-value", result.error["message"])

    def test_health_check_reports_actual_adapter_state(self):
        result = self.executor.execute(task_request("HEALTH_CHECK"))
        self.assertEqual(result.data["browser_connected"], True)
        self.assertEqual(result.data["aivio_available"], True)

    def test_browser_tasks_are_serialized_across_executor_instances(self):
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        first_request = task_request("HEALTH_CHECK")
        second_request = task_request("HEALTH_CHECK")

        class BlockingIntegration(FakeIntegration):
            def browser_health(self):
                first_entered.set()
                release_first.wait(timeout=2)
                return self.health

        class SecondIntegration(FakeIntegration):
            def browser_health(self):
                second_entered.set()
                return self.health

        first = TaskExecutor(integration=BlockingIntegration())
        second = TaskExecutor(integration=SecondIntegration())
        threads = [
            threading.Thread(target=first.execute, args=(first_request,)),
            threading.Thread(target=second.execute, args=(second_request,)),
        ]
        threads[0].start()
        self.assertTrue(first_entered.wait(timeout=1))
        self.assertEqual(first.get_task_result(first_request["task_id"])["status"], "running")
        threads[1].start()
        queued_result = second.get_task_result(second_request["task_id"])
        self.assertEqual(queued_result["status"], "queued")
        self.assertEqual(queued_result["started_at"], "")
        self.assertFalse(second_entered.wait(timeout=0.05))
        release_first.set()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertTrue(second_entered.is_set())
        self.assertEqual(second.get_task_result(second_request["task_id"])["status"], "completed")

    def test_browser_execution_lock_serializes_separate_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            started_path = os.path.join(directory, "started")
            acquired_path = os.path.join(directory, "acquired")
            child_code = (
                "import sys\n"
                "from pathlib import Path\n"
                "from scout.task_executor import _browser_execution_lock\n"
                "Path(sys.argv[1]).touch()\n"
                "with _browser_execution_lock('http://127.0.0.1:9223'):\n"
                "    Path(sys.argv[2]).touch()\n"
            )
            process = None
            with _browser_execution_lock("http://127.0.0.1:9223"):
                process = subprocess.Popen(
                    [sys.executable, "-c", child_code, started_path, acquired_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                deadline = time.monotonic() + 3
                while not os.path.exists(started_path) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(os.path.exists(started_path))
                time.sleep(0.1)
                self.assertFalse(os.path.exists(acquired_path))
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8"))
            self.assertTrue(os.path.exists(acquired_path), stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
