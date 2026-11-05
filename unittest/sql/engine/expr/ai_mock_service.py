#!/usr/bin/env python3
"""Local protocol fixture for AI-function SQL tests; not the contest's official service."""

import argparse
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class MockAIService(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, dimension=768):
        self.dimension = dimension
        self.requests = []
        self.request_lock = threading.Lock()
        super().__init__(address, MockAIHandler)


class MockAIHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if urlsplit(self.path).path != "/__requests":
            self.reply(404, {"error": "unknown path"})
            return
        with self.server.request_lock:
            events = list(self.server.requests)
        self.reply(200, events)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 8 * 1024 * 1024:
                raise ValueError("invalid body length")
            raw = self.rfile.read(length)
            parsed = urlsplit(self.path)
            if parsed.path == "/__echo":
                self.reply(200, {"length": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
                return
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            with self.server.request_lock:
                self.server.requests.append({"path": parsed.path, "body": request})
            fault = parse_qs(parsed.query).get("fault", [""])[0]
            if fault == "http_error":
                self.reply(503, {"error": "injected service failure"})
                return
            if fault == "invalid_json":
                raw_response = b"not json"
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw_response)))
                self.end_headers()
                self.wfile.write(raw_response)
                return
            if fault == "non_object":
                self.reply(200, [])
                return
            if not isinstance(request.get("model"), str) or not request["model"]:
                raise ValueError("model must be nonempty text")
            if parsed.path.endswith("/embeddings") or parsed.path == "/api/embed":
                response = self.embedding(request, parsed.path, fault)
            elif parsed.path.endswith("/chat/completions") or parsed.path == "/api/generate":
                response = self.completion(request, parsed.path, fault)
            elif parsed.path.endswith("/rerank"):
                response = self.rerank(request, fault)
            else:
                self.reply(404, {"error": "unknown path"})
                return
            self.reply(200, response)
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            self.reply(400, {"error": str(error)})

    def embedding(self, request, path, fault):
        contents = request.get("input")
        if isinstance(contents, str):
            contents = [contents]
        if not isinstance(contents, list) or not contents or not all(isinstance(x, str) for x in contents):
            raise ValueError("input must be text or a nonempty text array")
        dimension = request.get("dimensions", self.server.dimension)
        if type(dimension) is not int or not 0 < dimension <= 65536:
            raise ValueError("dimensions must be a positive integer no greater than 65536")
        vectors = []
        for content in contents:
            digest = hashlib.sha256(content.encode("utf-8")).digest()
            vectors.append([round((digest[i % len(digest)] - 128) / 128, 6) for i in range(dimension)])
        if fault == "wrong_dimension":
            vectors[-1] = vectors[-1][:-1]
        if fault == "bad_number":
            vectors[0][0] = "not a number"
        if path == "/api/embed":
            return {"model": request["model"], "embeddings": vectors}
        data = [{"index": i, "embedding": vector} for i, vector in enumerate(vectors)]
        if fault == "reverse":
            data.reverse()
        elif fault == "duplicate_index" and len(data) > 1:
            data[-1]["index"] = data[0]["index"]
        elif fault == "missing_index":
            data.pop()
        return {"model": request["model"], "data": data}

    def completion(self, request, path, fault):
        if request.get("stream", False) is not False:
            raise ValueError("fixture supports only nonstreaming generation")
        if path == "/api/generate":
            if request.get("stream") is not False:
                raise ValueError("Ollama requires explicit stream=false")
            prompt = request.get("prompt")
            options = request.get("options", {})
            if not isinstance(options, dict):
                raise ValueError("options must be an object")
        else:
            messages = request.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a nonempty array")
            prompt = messages[-1].get("content")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError("prompt must be nonempty text")
        content = "mock: " + prompt
        if fault == "empty_completion":
            content = ""
        elif fault == "null_completion":
            content = None
        if path == "/api/generate":
            return {"model": request["model"], "response": content, "done": True}
        return {"model": request["model"], "choices": [{"message": {"role": "assistant", "content": content}}]}

    def rerank(self, request, fault):
        query, documents = request.get("query"), request.get("documents")
        if not isinstance(query, str) or not isinstance(documents, list) or not documents:
            raise ValueError("query and nonempty documents are required")
        if not all(isinstance(x, str) for x in documents):
            raise ValueError("documents must be strings")
        top_n = request.get("top_n", len(documents))
        if type(top_n) is not int or not 0 < top_n <= len(documents):
            raise ValueError("invalid top_n")
        query_terms = set(query.casefold().split())
        rows = []
        for index, text in enumerate(documents):
            terms = set(text.casefold().split())
            score = len(terms & query_terms) / max(1, len(terms | query_terms))
            rows.append({"index": index, "relevance_score": score})
        rows.sort(key=lambda x: (-x["relevance_score"], x["index"]))
        rows = rows[:top_n]
        if fault == "reverse":
            rows.reverse()
        elif fault == "duplicate_index" and len(rows) > 1:
            rows[-1]["index"] = rows[0]["index"]
        elif fault == "missing_index":
            rows.pop()
        return {"results": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--dimension", type=int, default=768)
    args = parser.parse_args()
    server = MockAIService((args.host, args.port), args.dimension)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
