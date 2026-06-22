from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from lore_sdk import cli


class MockResponse:
    def __init__(self, payload=None, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return b"" if self.payload is None else json.dumps(self.payload).encode("utf-8")


ENV = {"LORE_BASE_URL": "https://lore.example.test", "LORE_API_KEY": "lore_test"}


def run(argv, payload, stdin: str | None = None):
    """Invoke the CLI with urlopen mocked; return (exit_code, stdout, stderr, last_request)."""
    out, err = io.StringIO(), io.StringIO()
    with patch.dict("os.environ", ENV, clear=False), patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = MockResponse(payload)
        ctx = patch("sys.stdin", io.StringIO(stdin)) if stdin is not None else None
        if ctx:
            ctx.start()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(argv)
        finally:
            if ctx:
                ctx.stop()
        request = urlopen.call_args.args[0] if urlopen.call_args else None
    return code, out.getvalue(), err.getvalue(), request


class CliTests(unittest.TestCase):
    def test_capture(self):
        code, out, _, req = run(
            ["capture", "Pixl renders garbage", "--lane", "project", "--confidence", "high"],
            {"capture_id": "inbox/2026/x"},
        )
        self.assertEqual(code, 0)
        self.assertIn("captured inbox/2026/x", out)
        self.assertEqual(req.get_method(), "POST")
        self.assertTrue(req.full_url.endswith("/api/memory/capture"))
        body = json.loads(req.data.decode())
        self.assertEqual(body["text"], "Pixl renders garbage")
        self.assertEqual(body["lane"], "project")
        self.assertEqual(body["metadata"]["confidence"], "high")

    def test_recall_text(self):
        payload = {
            "count": 1,
            "claims": [
                {
                    "recall_score": 0.71,
                    "subject": "services/lore",
                    "predicate": "is",
                    "object": "a memory backend",
                    "candidate_id": "c1",
                }
            ],
        }
        code, out, _, req = run(["recall", "memory backend", "--limit", "3"], payload)
        self.assertEqual(code, 0)
        self.assertIn("services/lore is a memory backend", out)
        self.assertIn("[c1]", out)
        self.assertEqual(req.get_method(), "GET")
        self.assertIn("/api/memory/recall?", req.full_url)
        self.assertIn("query=memory+backend", req.full_url)
        self.assertIn("limit=3", req.full_url)

    def test_recall_empty_surfaces_hint_on_stderr(self):
        payload = {
            "count": 0,
            "claims": [],
            "hint": "No matching memory for your actor, but 3 exist under other actors.",
        }
        code, out, err, _ = run(["recall", "nothing"], payload)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        self.assertIn("other actors", err)

    def test_recall_json(self):
        payload = {"count": 0, "claims": [], "hint": "x"}
        code, out, _, _ = run(["recall", "q", "--json"], payload)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), payload)

    def test_ack(self):
        code, out, _, req = run(["ack", "c1", "c2"], {"acknowledged_count": 2, "timestamp": "t"})
        self.assertEqual(code, 0)
        self.assertIn("acked 2", out)
        self.assertTrue(req.full_url.endswith("/api/memory/recall/ack"))
        self.assertEqual(json.loads(req.data.decode())["candidate_ids"], ["c1", "c2"])

    def test_search(self):
        payload = {"hits": [{"page": {"id": "services/lore", "title": "Lore"}}]}
        code, out, _, req = run(["search", "memory"], payload)
        self.assertEqual(code, 0)
        self.assertIn("services/lore", out)
        self.assertIn("Lore", out)
        self.assertIn("/api/search", req.full_url)

    def test_read(self):
        code, out, _, req = run(["read", "services/lore"], {"id": "services/lore", "content": "# Lore\n\nbody"})
        self.assertEqual(code, 0)
        self.assertIn("# Lore", out)
        self.assertTrue(req.full_url.endswith("/api/pages/services/lore"))

    def test_write_stdin(self):
        code, out, _, req = run(["write", "runbooks/x", "-m", "msg"], {"id": "runbooks/x"}, stdin="# hi\n")
        self.assertEqual(code, 0)
        self.assertIn("wrote runbooks/x", out)
        self.assertEqual(req.get_method(), "PUT")
        body = json.loads(req.data.decode())
        self.assertEqual(body["content"], "# hi\n")
        self.assertEqual(body["commit_message"], "msg")

    def test_whoami(self):
        code, out, _, req = run(["whoami"], {"actor": "dev", "role": "admin", "auth_mode": "api_key"})
        self.assertEqual(code, 0)
        self.assertIn("actor=dev", out)
        self.assertIn("role=admin", out)
        self.assertTrue(req.full_url.endswith("/api/whoami"))

    def test_base_url_from_env(self):
        _, _, _, req = run(["whoami"], {"actor": "dev"})
        self.assertTrue(req.full_url.startswith("https://lore.example.test"))

    def test_error_is_reported_and_nonzero(self):
        from lore_sdk.exceptions import LoreError

        with patch.dict("os.environ", ENV, clear=False), patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = LoreError(404, "not found")
            err = io.StringIO()
            with redirect_stderr(err):
                code = cli.main(["read", "missing/page"])
        self.assertEqual(code, 1)
        self.assertIn("lore:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
