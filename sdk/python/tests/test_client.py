from __future__ import annotations

import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from lore_sdk import LoreClient, LoreError


class MockResponse:
    def __init__(self, payload: dict | list | None = None, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def read(self):
        if self.payload is None:
            return b""
        return json.dumps(self.payload).encode("utf-8")


class LoreClientTests(unittest.TestCase):
    def test_client_initialization(self):
        client = LoreClient("https://lore.example.test/", auth_token="secret", timeout=10)

        self.assertEqual(client.base_url, "https://lore.example.test")
        self.assertEqual(client.auth_token, "secret")
        self.assertEqual(client.timeout, 10)

    @patch("urllib.request.urlopen")
    def test_get_request_list_pages(self, urlopen):
        urlopen.return_value = MockResponse([{"id": "projects/example-project"}])
        client = LoreClient("https://lore.example.test")

        result = client.list_pages(kind="project", tag="gpu")

        self.assertEqual(result, [{"id": "projects/example-project"}])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/pages?tag=gpu&kind=project")
        self.assertEqual(request.get_method(), "GET")

    @patch("urllib.request.urlopen")
    def test_post_request_create_capture(self, urlopen):
        urlopen.return_value = MockResponse({"page": {"id": "inbox/capture"}})
        client = LoreClient("https://lore.example.test")

        result = client.create_capture("GPU note", "Observed a deployment detail.", "README.md", tags=["gpu"])

        self.assertEqual(result["page"]["id"], "inbox/capture")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/capture")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "title": "GPU note",
                "body": "Observed a deployment detail.",
                "source": "README.md",
                "tags": ["gpu"],
                "observation": "Observed a deployment detail.",
                "sources": ["README.md"],
            },
        )

    @patch("urllib.request.urlopen")
    def test_put_request_upsert_page(self, urlopen):
        urlopen.return_value = MockResponse({"id": "services/lore"})
        client = LoreClient("https://lore.example.test")

        result = client.upsert_page("services/lore", "# Lore", commit_message="Update lore")

        self.assertEqual(result["id"], "services/lore")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/pages/services/lore")
        self.assertEqual(request.get_method(), "PUT")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"content": "# Lore", "commit_message": "Update lore"})

    @patch("urllib.request.urlopen")
    def test_post_request_create_trace(self, urlopen):
        urlopen.return_value = MockResponse({"trace_id": "trace-abc", "actor": "nyx"})
        client = LoreClient("https://lore.example.test")

        result = client.create_trace(actor="nyx", reason_summary="Selected low-risk patch.")

        self.assertEqual(result["trace_id"], "trace-abc")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/traces")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {
                "actor": "nyx",
                "reason_summary": "Selected low-risk patch.",
                "status": "active",
            },
        )

    @patch("urllib.request.urlopen")
    def test_get_request_get_trace(self, urlopen):
        urlopen.return_value = MockResponse({"trace_id": "trace-abc"})
        client = LoreClient("https://lore.example.test")

        result = client.get_trace("trace-abc")

        self.assertEqual(result["trace_id"], "trace-abc")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/traces/trace-abc")
        self.assertEqual(request.get_method(), "GET")

    @patch("urllib.request.urlopen")
    def test_get_request_list_traces(self, urlopen):
        urlopen.return_value = MockResponse({"traces": []})
        client = LoreClient("https://lore.example.test")

        result = client.list_traces(actor="nyx")

        self.assertEqual(result["traces"], [])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/traces?limit=20&actor=nyx")
        self.assertEqual(request.get_method(), "GET")

    @patch("urllib.request.urlopen")
    def test_delete_request_delete_page(self, urlopen):
        urlopen.return_value = MockResponse(None, status=204)
        client = LoreClient("https://lore.example.test")

        result = client.delete_page("services/lore")

        self.assertEqual(result, {})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://lore.example.test/api/pages/services/lore")
        self.assertEqual(request.get_method(), "DELETE")

    @patch("urllib.request.urlopen")
    def test_404_error_handling(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://lore.example.test/api/pages/missing",
            404,
            "Not Found",
            hdrs={},
            fp=BytesIO(b'{"detail":"Lore page not found."}'),
        )
        client = LoreClient("https://lore.example.test")

        with self.assertRaises(LoreError) as raised:
            client.get_page("missing")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.message, "Lore page not found.")

    @patch("urllib.request.urlopen")
    def test_500_error_handling(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://lore.example.test/api/lint",
            500,
            "Internal Server Error",
            hdrs={},
            fp=BytesIO(b"server exploded"),
        )
        client = LoreClient("https://lore.example.test")

        with self.assertRaises(LoreError) as raised:
            client.lint()

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.message, "server exploded")

    @patch("urllib.request.urlopen")
    def test_auth_header_inclusion_when_token_provided(self, urlopen):
        urlopen.return_value = MockResponse({"ok": True})
        client = LoreClient("https://lore.example.test", auth_token="token-123")

        client.health()

        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer token-123")

    @patch("urllib.request.urlopen")
    def test_timeout_setting(self, urlopen):
        urlopen.return_value = MockResponse({"ok": True})
        client = LoreClient("https://lore.example.test", timeout=7)

        client.health()

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
