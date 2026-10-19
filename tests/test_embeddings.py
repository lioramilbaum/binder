import json
import threading
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, Mock, patch

from binder.config import EMBEDDING_MAX_CHARS
from binder.embeddings import (
    EmbeddingUnavailable,
    EmbeddingWorker,
    OllamaClient,
    embedding_text,
    vector_literal,
)


def ok_response(payload):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    return response


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class OllamaClientTests(unittest.TestCase):
    def client(self, model="bge-m3", dimensions=3):
        return OllamaClient("http://ollama.test:11434/", model, dimensions)

    def test_posts_to_api_embed_with_model_and_no_prefix_for_bge_m3(self):
        client = self.client()
        with patch("binder.embeddings.urllib.request.urlopen") as urlopen:
            urlopen.return_value = ok_response({"embeddings": [[0.1, 0.2, 0.3]]})

            vectors = client.embed(["חוזה שכירות"], kind="doc", timeout=7)

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://ollama.test:11434/api/embed")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        body = json.loads(request.data)
        self.assertEqual(body, {"model": "bge-m3", "input": ["חוזה שכירות"], "truncate": True})
        self.assertEqual(vectors, [[0.1, 0.2, 0.3]])

    def test_nomic_model_gets_task_prefixes(self):
        client = self.client(model="nomic-embed-text")
        with patch("binder.embeddings.urllib.request.urlopen") as urlopen:
            urlopen.return_value = ok_response({"embeddings": [[1.0, 0.0, 0.0]]})

            client.embed(["lease"], kind="query", timeout=3)
            query_body = json.loads(urlopen.call_args.args[0].data)
            urlopen.return_value = ok_response({"embeddings": [[1.0, 0.0, 0.0]]})
            client.embed(["lease"], kind="doc", timeout=3)
            doc_body = json.loads(urlopen.call_args.args[0].data)

        self.assertEqual(query_body["input"], ["search_query: lease"])
        self.assertEqual(doc_body["input"], ["search_document: lease"])

    def test_connection_errors_become_embedding_unavailable(self):
        for error in (urllib.error.URLError("refused"), TimeoutError("slow")):
            with self.subTest(error=type(error).__name__):
                with patch("binder.embeddings.urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(EmbeddingUnavailable):
                        self.client().embed(["x"], timeout=1)

    def test_wrong_dimension_becomes_embedding_unavailable(self):
        with patch("binder.embeddings.urllib.request.urlopen") as urlopen:
            urlopen.return_value = ok_response({"embeddings": [[0.1, 0.2]]})

            with self.assertRaises(EmbeddingUnavailable):
                self.client().embed(["x"], timeout=1)

    def test_malformed_json_becomes_embedding_unavailable(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"<html>not json</html>"
        with patch("binder.embeddings.urllib.request.urlopen", return_value=response):
            with self.assertRaises(EmbeddingUnavailable):
                self.client().embed(["x"], timeout=1)

    def test_non_finite_values_are_rejected(self):
        with patch("binder.embeddings.urllib.request.urlopen") as urlopen:
            urlopen.return_value = ok_response({"embeddings": [[0.1, float("nan"), 0.3]]})

            with self.assertRaises(EmbeddingUnavailable):
                self.client().embed(["x"], timeout=1)

    def test_circuit_breaker_skips_calls_during_cooldown(self):
        client = self.client()
        start = 1000.0
        with patch("binder.embeddings.urllib.request.urlopen", side_effect=urllib.error.URLError("down")) as urlopen:
            with patch("binder.embeddings.time.monotonic", return_value=start):
                with self.assertRaises(EmbeddingUnavailable):
                    client.embed(["first"], timeout=1)
            with patch("binder.embeddings.time.monotonic", return_value=start + 5):
                with self.assertRaises(EmbeddingUnavailable):
                    client.embed(["second"], timeout=1)
            self.assertEqual(urlopen.call_count, 1)

            urlopen.side_effect = None
            urlopen.return_value = ok_response({"embeddings": [[0.1, 0.2, 0.3]]})
            with patch("binder.embeddings.time.monotonic", return_value=start + 31):
                self.assertEqual(client.embed(["third"], timeout=1), [[0.1, 0.2, 0.3]])
            self.assertEqual(urlopen.call_count, 2)


class TextHelperTests(unittest.TestCase):
    def test_vector_literal_round_trips(self):
        values = [0.1, -2.5e-05, 3.0, 1234.5678]

        literal = vector_literal(values)

        self.assertTrue(literal.startswith("[") and literal.endswith("]"))
        self.assertEqual([float(part) for part in literal[1:-1].split(",")], values)

    def test_embedding_text_orders_metadata_before_body(self):
        document = {
            "name": "Lease",
            "description": "Apartment",
            "summary": "Renewal terms",
            "tags": ["housing", "legal"],
            "content": "Body text",
        }

        text = embedding_text(document, ["Attachment words"])

        self.assertEqual(
            text,
            "Lease\nApartment\nRenewal terms\nhousing, legal\nBody text\nAttachment words",
        )

    def test_embedding_text_truncation_keeps_the_name(self):
        document = {"name": "Lease", "content": "x" * (EMBEDDING_MAX_CHARS * 2)}

        text = embedding_text(document, [])

        self.assertEqual(len(text), EMBEDDING_MAX_CHARS)
        self.assertTrue(text.startswith("Lease\n"))


class EmbeddingWorkerTests(unittest.TestCase):
    def setUp(self):
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            if worker.is_alive():
                worker.stop()

    def make_worker(self):
        worker = EmbeddingWorker(Mock(), Mock(model="bge-m3"))
        worker.debounce_seconds = 0
        self.workers.append(worker)
        return worker

    def test_notify_triggers_embed_pending(self):
        calls = []
        with patch("binder.embeddings.embed_pending", side_effect=lambda *a, **k: calls.append(1) or (0, 0)):
            worker = self.make_worker()
            worker.start()
            self.assertTrue(wait_for(lambda: len(calls) >= 1))
            calls.clear()
            time.sleep(0.05)
            worker.notify()

            self.assertTrue(wait_for(lambda: len(calls) >= 1))

    def test_exception_does_not_kill_the_thread(self):
        with patch("binder.embeddings.embed_pending", side_effect=[RuntimeError("boom"), (1, 0), (0, 0)]) as embed:
            worker = self.make_worker()
            worker.start()
            self.assertTrue(wait_for(lambda: embed.call_count >= 1))
            worker.notify()

            self.assertTrue(wait_for(lambda: embed.call_count >= 2))
            self.assertTrue(worker.is_alive())

    def test_outage_is_logged_once_and_worker_keeps_running(self):
        with patch("binder.embeddings.embed_pending", side_effect=EmbeddingUnavailable("down")) as embed:
            with self.assertLogs("binder.embeddings", level="WARNING") as logs:
                worker = self.make_worker()
                worker.start()
                self.assertTrue(wait_for(lambda: embed.call_count >= 1))
                worker.notify()
                self.assertTrue(wait_for(lambda: embed.call_count >= 2))
        self.assertEqual(len(logs.records), 1)

    def test_stop_joins_the_thread(self):
        with patch("binder.embeddings.embed_pending", return_value=(0, 0)):
            worker = self.make_worker()
            worker.start()
            worker.stop()

        self.assertFalse(worker.is_alive())
        self.assertIsInstance(worker, threading.Thread)


if __name__ == "__main__":
    unittest.main()
