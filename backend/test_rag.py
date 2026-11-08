"""URL取得、ベクトル検索、およびCLIの動作を検証する。"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from langchain_chroma import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding
import numpy as np
import requests

import rag


class Response:
    """テストでrequestsのレスポンスとして使用する簡易オブジェクト。"""

    def __init__(self, status=200, body=b"", headers=None):
        """レスポンスのステータス、本文、ヘッダーを設定する。"""
        self.status_code = status
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        """コンテキストマネージャー用に自分自身を返す。"""
        return self

    def __exit__(self, *args):
        """コンテキスト終了時には追加の処理を行わない。"""
        pass

    def raise_for_status(self):
        """HTTPエラーステータスの場合に例外を送出する。"""
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size):
        """レスポンス本文を単一チャンクとして返す。"""
        yield self.body


class FetchTests(unittest.TestCase):
    """robots.txtに従ったHTML取得を検証する。"""

    def setUp(self):
        """一時保存先と待機を省略した取得処理を用意する。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.dir_patch = patch.object(rag, "CHROMA_DIR", Path(self.directory.name))
        self.dir_patch.start()
        self.addCleanup(self.dir_patch.stop)
        self.sleep_patch = patch.object(rag.time, "sleep")
        self.sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.fetcher = rag.SafeFetcher()

    def fetch_with(self, responses, url="https://example.com/page"):
        """指定された模擬レスポンスを使い、取得結果とアクセス先を返す。"""
        calls = []

        def get(target, **kwargs):
            """アクセス先を記録し、対応する模擬レスポンスを返す。"""
            calls.append(target)
            return responses[target]

        with patch.object(self.fetcher.session, "get", side_effect=get):
            result = self.fetcher.fetch(url)
        return result, calls

    def test_allowed_html_and_spacing(self):
        """許可されたHTMLとクローラー向け待機間隔を確認する。"""
        responses = {
            "https://example.com/robots.txt": Response(body=b"User-agent: *\nDisallow: /private\nCrawl-delay: 3"),
            "https://example.com/page": Response(
                body=b"<html><script>hidden</script><p>Hello</p></html>",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
        }
        result, calls = self.fetch_with(responses)
        self.assertEqual(result, ("https://example.com/page", "Hello"))
        self.assertEqual(calls, list(responses))
        self.assertGreater(self.sleep.call_args.args[0], 2)

    def test_disallowed_page_is_never_requested(self):
        """robots.txtで禁止されたページにアクセスしないことを確認する。"""
        responses = {
            "https://example.com/robots.txt": Response(body=b"User-agent: *\nDisallow: /private*"),
        }
        with self.assertRaisesRegex(ValueError, "禁止"):
            self.fetch_with(responses, "https://example.com/private/file")

    def test_redirect_checks_destination_robots_before_page(self):
        """リダイレクト先のrobots.txtをページより先に確認する。"""
        responses = {
            "https://example.com/robots.txt": Response(body=b"User-agent: *\nDisallow:"),
            "https://example.com/page": Response(status=302, headers={"Location": "https://other.example/secret"}),
            "https://other.example/robots.txt": Response(body=b"User-agent: *\nDisallow: /secret"),
        }
        calls = []
        with patch.object(self.fetcher.session, "get", side_effect=lambda url, **kwargs: calls.append(url) or responses[url]):
            with self.assertRaisesRegex(ValueError, "禁止"):
                self.fetcher.fetch("https://example.com/page")
        self.assertEqual(calls, list(responses))

    def test_missing_or_unparseable_robots_stops_fetch(self):
        """robots.txtの取得または解釈に失敗した場合は停止する。"""
        for response in (Response(status=404), Response(body=b"not robots")):
            with self.subTest(response=response):
                with patch.object(self.fetcher.session, "get", return_value=response) as get:
                    with self.assertRaises((ValueError, requests.HTTPError)):
                        self.fetcher.fetch("https://example.com/page")
                    self.assertEqual(get.call_count, 1)

    def test_retry_after_stops_before_page(self):
        """Retry-Afterを受信した場合はページ取得を行わない。"""
        response = Response(status=503, headers={"Retry-After": "120"})
        with patch.object(self.fetcher.session, "get", return_value=response) as get:
            with self.assertRaisesRegex(ValueError, "Retry-After"):
                self.fetcher.fetch("https://example.com/page")
            self.assertEqual(get.call_count, 1)

    def test_request_rate_is_persisted_for_later_fetchers(self):
        """Request-rateが後続の取得処理にも引き継がれることを確認する。"""
        responses = {
            "https://example.com/robots.txt": Response(body=b"User-agent: *\nRequest-rate: 2/10"),
            "https://example.com/page": Response(body=b"<p>text</p>", headers={"Content-Type": "text/html"}),
        }
        self.fetch_with(responses)
        self.assertEqual(rag.SafeFetcher().read_state()["example.com"]["interval"], 10)

    def test_other_agent_delay_does_not_block_fetch(self):
        """別のUser-Agentに設定された待機条件では取得を止めない。"""
        responses = {
            "https://example.com/robots.txt": Response(
                body=b"User-agent: otherbot\nCrawl-delay: 5\nUser-agent: *\nDisallow:\n"
            ),
            "https://example.com/page": Response(body=b"<p>text</p>", headers={"Content-Type": "text/html"}),
        }
        self.fetch_with(responses)

    def test_unsupported_visit_time_stops_fetch(self):
        """未対応のVisit-timeが指定された場合は取得を停止する。"""
        response = Response(body=b"User-agent: *\nVisit-time: 0600-1200\n")
        with patch.object(self.fetcher.session, "get", return_value=response) as get:
            with self.assertRaisesRegex(ValueError, "visit-time"):
                self.fetcher.fetch("https://example.com/page")
            self.assertEqual(get.call_count, 1)


class VectorStoreTests(unittest.TestCase):
    """文書の更新、永続化、検索、埋め込み設定を検証する。"""

    def test_ingest_replace_persist_and_search(self):
        """再取り込み時の古いチャンク削除と永続化後の検索を確認する。"""
        with tempfile.TemporaryDirectory() as directory:
            embedding = DeterministicFakeEmbedding(size=8)
            store = Chroma(collection_name="test_rag", persist_directory=directory, embedding_function=embedding)
            source = "https://example.com/page"
            with patch.object(rag.SafeFetcher, "fetch", return_value=(source, "a" * 2500)):
                self.assertGreater(rag.ingest(source, store)[1], 1)
            with patch.object(rag.SafeFetcher, "fetch", return_value=(source, "new text")):
                self.assertEqual(rag.ingest(source, store), (source, 1))
            reopened = Chroma(collection_name="test_rag", persist_directory=directory, embedding_function=embedding)
            self.assertEqual(len(reopened.get()["ids"]), 1)
            results = rag.search("new", reopened, 4)
            self.assertEqual([(doc.page_content, doc.metadata["source"]) for doc in results], [("new text", source)])

    def test_invalid_queries(self):
        """空の検索語と不正な検索件数を拒否することを確認する。"""
        for query, limit in (("", 4), ("text", 0)):
            with self.subTest(query=query, limit=limit), self.assertRaises(ValueError):
                rag.search(query, None, limit)

    def test_model_mismatch_is_rejected(self):
        """保存済みコレクションと異なる埋め込みモデルを拒否する。"""
        with tempfile.TemporaryDirectory() as directory, patch.object(rag, "CHROMA_DIR", Path(directory)):
            with patch.object(rag, "EMBEDDING_MODEL", "first"):
                rag.vector_store()
            with patch.object(rag, "EMBEDDING_MODEL", "second"), self.assertRaisesRegex(ValueError, "モデル"):
                rag.vector_store()

    def test_chroma_backend_uses_separate_persistent_collection(self):
        """Chroma方式のデータがOllama方式と分離して永続化されることを確認する。"""
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(rag, "CHROMA_DIR", Path(directory)), \
             patch.dict(os.environ, {"EMBEDDING_BACKEND": "chroma"}), \
             patch.object(rag, "DefaultEmbeddingFunction") as embedding_factory:
            embedding = embedding_factory.return_value
            embedding.side_effect = lambda texts: [np.array([1.0, 0.0]) for _ in texts]
            source = "https://example.com/page"
            with patch.object(rag.SafeFetcher, "fetch", return_value=(source, "chroma text")):
                rag.ingest(source, rag.vector_store())
            results = rag.search("query", rag.vector_store(), 4)
            self.assertEqual(results[0].page_content, "chroma text")
            self.assertEqual(results[0].metadata["source"], source)
            self.assertEqual(embedding.call_count, 2)
            with patch.dict(os.environ, {"EMBEDDING_BACKEND": "ollama"}):
                self.assertEqual(rag.vector_store().get()["ids"], [])

    def test_invalid_embedding_backend_is_rejected(self):
        """未対応の埋め込み方式を拒否することを確認する。"""
        with patch.dict(os.environ, {"EMBEDDING_BACKEND": "unknown"}):
            with self.assertRaisesRegex(ValueError, "EMBEDDING_BACKEND"):
                rag.vector_store()


class CommandLineTests(unittest.TestCase):
    """ローカルの模擬サービスを使ってCLIを検証する。"""

    def test_ingest_and_search_with_local_services(self):
        """CLIからURLを取り込み、別プロセスで検索できることを確認する。"""
        class Handler(BaseHTTPRequestHandler):
            """HTML、robots.txt、および模擬Ollama埋め込みを提供する。"""

            def do_GET(self):
                """テスト用のrobots.txtまたはHTMLを返す。"""
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nDisallow:\n"
                    content_type = "text/plain"
                else:
                    body = b"<html><p>Local integration example</p></html>"
                    content_type = "text/html"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                """Ollama埋め込みAPIを模擬したベクトルを返す。"""
                self.assert_path()
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                vectors = [[1.0, 0.0, 0.0] for _ in payload["input"]]
                body = json.dumps({"model": payload["model"], "embeddings": vectors}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def assert_path(self):
                """埋め込みAPI以外のパスへのPOSTを拒否する。"""
                if self.path != "/api/embed":
                    raise ValueError(f"Unexpected path: {self.path}")

            def log_message(self, format, *args):
                """テスト用サーバーのアクセスログを抑制する。"""
                pass

        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}"
                env = os.environ.copy()
                env.update(CHROMA_DIR=directory, OLLAMA_BASE_URL=base_url, EMBEDDING_MODEL="test", EMBEDDING_BACKEND="ollama")
                script = str(Path(rag.__file__))
                backends = ["ollama"]
                if os.environ.get("TEST_CHROMA_MODEL") == "1":
                    backends.append("chroma")
                for backend in backends:
                    with self.subTest(backend=backend):
                        env["EMBEDDING_BACKEND"] = backend
                        ingest = subprocess.run(
                            [sys.executable, script, "ingest", f"{base_url}/page", "--confirm-permission"],
                            env=env, text=True, capture_output=True, timeout=120,
                        )
                        self.assertEqual(ingest.returncode, 0, ingest.stderr)
                        result = subprocess.run(
                            [sys.executable, script, "search", "example"],
                            env=env, text=True, capture_output=True, timeout=120,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn(f"{base_url}/page", result.stdout)
                        self.assertIn("Local integration example", result.stdout)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
