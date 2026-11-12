import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

import server as app


class OperationsApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "test.db"
        app.DB_PATH = database
        app.IDENTITY_DB_PATH = Path(self.temporary.name) / "identity.db"
        app.IDENTITY_SECRET_PATH = Path(self.temporary.name) / "identity.secret"
        app.TENANT_DB_DIR = Path(self.temporary.name) / "tenants"
        app.INITIALIZED_TENANT_DBS.clear()
        app.SECRET_PATH = database.with_suffix(".secret")
        app.init_db()
        app.init_identity_db()
        app.migrate_legacy_identity()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.admin = self.client("admin", "admin123")

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def client(self, username, password):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request(opener, "/api/login", "POST", {"username": username, "password": password})
        return opener

    def request(self, client, path, method="GET", data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with client.open(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            try:
                return error.code, error.read()
            finally:
                error.close()

    def create_occurrence(self, product, sector="Testes", quantity=2, unit_value=10):
        status, body = self.request(self.admin, "/api/occurrences", "POST", {
            "product": product, "quantity": quantity, "unit_value": unit_value,
            "occurrence_date": date.today().isoformat(), "sector": sector, "aisle": "T-01",
            "reason": "Embalagem danificada", "classification": "Avaria", "responsible": "QA",
        })
        self.assertEqual(status, 201, body)
        return json.loads(body)["id"]

    def occurrence(self, occurrence_id, client=None):
        status, body = self.request(client or self.admin, f"/api/occurrences?id={occurrence_id}")
        self.assertEqual(status, 200, body)
        return json.loads(body)["occurrences"][0]

    def summary(self, sector):
        status, body = self.request(self.admin, f"/api/summary?sector={sector}")
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_split_reclassifies_without_double_counting(self):
        occurrence_id = self.create_occurrence("Split QA", quantity=3)
        source = self.occurrence(occurrence_id)["allocations"][0]
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}/split", "POST", {
            "allocation_id": source["id"], "quantity": 1, "classification": "Fornecedor",
        })
        self.assertEqual(status, 200, body)
        occurrence = self.occurrence(occurrence_id)
        self.assertEqual(sum(item["quantity"] for item in occurrence["allocations"]), 3)
        self.assertEqual(self.summary("Testes")["recorded_total"], 30)
        self.assertEqual(self.summary("Testes")["gross"], 20)
        status, body = self.request(self.admin, "/api/summary?sector=Testes&classification=Fornecedor")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["recorded_total"], 10)
        self.assertEqual(json.loads(body)["gross"], 0)

    def test_cancelled_occurrence_stays_auditable_but_leaves_financials(self):
        occurrence_id = self.create_occurrence("Cancel QA")
        self.assertEqual(self.summary("Testes")["gross"], 20)
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}/cancel", "POST", {"reason": "Duplicado"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.summary("Testes")["gross"], 0)
        occurrence = self.occurrence(occurrence_id)
        self.assertEqual(occurrence["record_status"], "cancelled")
        self.assertEqual(occurrence["history"][0]["action"], "Lançamento cancelado")

    def test_draft_requires_operational_fields_before_activation(self):
        status, body = self.request(self.admin, "/api/occurrences", "POST", {"record_status": "draft"})
        self.assertEqual(status, 201, body)
        occurrence_id = json.loads(body)["id"]
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", {"record_status": "active"})
        self.assertEqual(status, 400, body)
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", {
            "record_status": "active", "product": "Draft QA", "sector": "Testes",
            "reason": "Embalagem", "responsible": "QA",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(self.occurrence(occurrence_id)["record_status"], "active")

    def test_financial_edit_requires_reason_and_quantity_balance(self):
        occurrence_id = self.create_occurrence("Edit QA")
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", {"quantity": 3})
        self.assertEqual(status, 400, body)
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", {"unit_value": 12})
        self.assertEqual(status, 400, body)
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", {
            "unit_value": 12, "change_reason": "Correção conferida",
        })
        self.assertEqual(status, 200, body)
        occurrence = self.occurrence(occurrence_id)
        change = next(item for item in occurrence["history"] if item["action"] == "Lançamento original atualizado")
        details = json.loads(change["details"])
        self.assertEqual(details["unit_value"], {"before": 10.0, "after": 12.0})

    def test_reclassification_requires_evidence_and_updates_gross(self):
        occurrence_id = self.create_occurrence("Reclass QA")
        allocation = self.occurrence(occurrence_id)["allocations"][0]
        payload = {"classification": "Fornecedor", "allocation_id": allocation["id"], "change_reason": "Solicitação ao fornecedor"}
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", payload)
        self.assertEqual(status, 400, body)
        payload["evidence"] = "PROTO-34"
        status, body = self.request(self.admin, f"/api/occurrences/{occurrence_id}", "PATCH", payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.summary("Testes")["gross"], 0)
        self.assertEqual(self.summary("Testes")["recorded_total"], 20)

    def test_analyst_cannot_approve_discount(self):
        occurrence_id = self.create_occurrence("Permission QA")
        allocation = self.occurrence(occurrence_id)["allocations"][0]
        analyst = self.client("analista", "analista123")
        status, body = self.request(analyst, f"/api/allocations/{allocation['id']}", "PATCH", {
            "decision": "include", "status": "approved", "rationale": "Tentativa sem aprovação",
        })
        self.assertEqual(status, 403, body)


if __name__ == "__main__":
    unittest.main()
