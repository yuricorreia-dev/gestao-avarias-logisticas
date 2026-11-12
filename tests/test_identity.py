import http.cookiejar
import json
import tempfile
import urllib.error
import urllib.request
import unittest
import uuid
from pathlib import Path

import server


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        server.DB_PATH = root / "legacy.db"
        server.IDENTITY_DB_PATH = root / "identity.db"
        server.IDENTITY_SECRET_PATH = root / "identity.secret"
        server.TENANT_DB_DIR = root / "tenants"
        server.INITIALIZED_TENANT_DBS.clear()
        server.init_db(seed_users=True, seed_demo=True)
        server.init_identity_db(); server.migrate_legacy_identity()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        import threading
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(); self.temp.cleanup()

    def request(self, path, method="GET", data=None, client=None):
        client = client or urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None,
            method=method, headers={"Content-Type": "application/json"})
        try:
            with client.open(req) as response: return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_new_corporation_has_empty_isolated_database(self):
        owner = f"owner-{uuid.uuid4().hex[:8]}@example.com"
        status, result = self.request("/api/corporations", "POST", {"legal_name": "Corp A", "display_name": "Corp A", "email": "corp-a@example.com", "owner_email": owner, "owner_name": "Owner A", "owner_password": "SenhaSegura123", "address": "Rua A", "city": "São Paulo", "state": "SP"})
        self.assertEqual(status, 201); self.assertIn("verification_url", result)
        self.assertEqual(self.request(result["verification_url"].replace(self.base, ""))[0], 200)
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, login = self.request("/api/login", "POST", {"username": owner, "password": "SenhaSegura123"}, client)
        self.assertEqual(status, 200); self.assertEqual(login["user"]["role_code"], "owner")
        status, summary = self.request("/api/summary", client=client)
        self.assertEqual(status, 200); self.assertEqual(summary["recorded_total"], 0); self.assertEqual(summary["gross"], 0)

    def test_legacy_login_is_migrated(self):
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, login = self.request("/api/login", "POST", {"username": "admin", "password": "admin123"}, client)
        self.assertEqual(status, 200); self.assertEqual(login["user"]["corporation_id"], "legacy")
        self.assertIn("manage_users", login["user"]["permissions"])


if __name__ == "__main__": unittest.main()
