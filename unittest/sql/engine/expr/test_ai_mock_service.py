"""Protocol checks for the local fixture; these do not test seekdb SQL execution."""
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from ai_mock_service import MockAIService


class MockProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = MockAIService(("127.0.0.1", 0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        # Only this loopback fixture uses a direct opener; external traffic is not sent here.
        cls.http = build_opener(ProxyHandler({}))
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def post(self, path, body):
        req = Request(self.base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        with self.http.open(req, timeout=5) as response:
            return json.load(response)

    def test_completion_preserves_unicode_and_long_text(self):
        text = "你好🌊" * 10000
        result = self.post("/compatible-mode/v1/chat/completions",
                           {"model": "arbitrary-model", "messages": [{"role": "user", "content": text}]})
        self.assertEqual(result["choices"][0]["message"]["content"], "mock: " + text)

    def test_embedding_default_and_explicit_dimensions(self):
        for dimension in (None, 4, 768):
            body = {"model": "arbitrary-model", "input": ["one", "two"]}
            if dimension is not None:
                body["dimensions"] = dimension
            rows = self.post("/compatible-mode/v1/embeddings", body)["data"]
            self.assertEqual([row["index"] for row in rows], [0, 1])
            self.assertTrue(all(len(row["embedding"]) == (768 if dimension is None else dimension) for row in rows))

    def test_vectors_do_not_depend_on_batch_position(self):
        first = self.post("/v1/embeddings", {"model": "m", "input": ["a", "b"], "dimensions": 4})
        second = self.post("/v1/embeddings", {"model": "m", "input": ["b", "a"], "dimensions": 4})
        self.assertEqual(first["data"][0]["embedding"], second["data"][1]["embedding"])

    def test_response_faults_are_injected(self):
        body = {"model": "m", "input": ["a", "b"], "dimensions": 4}
        self.assertEqual([x["index"] for x in self.post("/v1/embeddings?fault=reverse", body)["data"]], [1, 0])
        duplicate = self.post("/v1/embeddings?fault=duplicate_index", body)["data"]
        self.assertEqual(duplicate[0]["index"], duplicate[1]["index"])
        self.assertEqual(len(self.post("/v1/embeddings?fault=missing_index", body)["data"]), 1)
        self.assertEqual(len(self.post("/v1/embeddings?fault=wrong_dimension", body)["data"][-1]["embedding"]), 3)
        self.assertIsInstance(self.post("/v1/embeddings?fault=bad_number", body)["data"][0]["embedding"][0], str)
        self.assertEqual(self.post("/v1/embeddings?fault=non_object", body), [])

    def test_ollama_native_protocol(self):
        completion = self.post("/api/generate", {"model": "any", "prompt": "hello", "stream": False,
                                               "options": {"temperature": 0.2, "num_predict": 16}})
        self.assertTrue(completion["done"])
        self.assertEqual(completion["response"], "mock: hello")
        embed = self.post("/api/embed", {"model": "any", "input": ["hello"], "dimensions": 4})
        self.assertEqual(len(embed["embeddings"][0]), 4)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/generate", {"model": "any", "prompt": "hello"})
        self.assertEqual(error.exception.code, 400)

    def test_rerank_returns_every_requested_document_and_stable_ties(self):
        rows = self.post("/v1/rerank", {"model": "any", "query": "apple", "top_n": 23,
                                      "documents": ["banana"] * 22 + ["apple"]})["results"]
        self.assertEqual(len(rows), 23)
        self.assertEqual(rows[0]["index"], 22)
        self.assertEqual([r["index"] for r in rows[1:]], list(range(22)))

    def test_empty_completion_and_service_failure(self):
        body = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        self.assertEqual(self.post("/v1/chat/completions?fault=empty_completion", body)["choices"][0]["message"]["content"], "")
        self.assertIsNone(self.post("/v1/chat/completions?fault=null_completion", body)["choices"][0]["message"]["content"])
        with self.assertRaises(HTTPError) as error:
            self.post("/v1/chat/completions?fault=http_error", body)
        self.assertEqual(error.exception.code, 503)

    def test_invalid_json_fault_returns_http_success_with_unparseable_body(self):
        with self.assertRaises(json.JSONDecodeError):
            self.post("/v1/embeddings?fault=invalid_json", {"model": "m", "input": ["a"]})

    def test_invalid_inputs(self):
        for body in (["not an object"], {"model": "m", "input": [None]},
                     {"model": "m", "input": "text", "dimensions": True}):
            with self.assertRaises(HTTPError) as error:
                self.post("/v1/embeddings", body)
            self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
