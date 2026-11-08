"""URLからのデータ取り込みとローカルChromaDBの類似検索を提供する。"""

import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import chromadb
import requests
from bs4 import BeautifulSoup
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from protego import Protego

USER_AGENT = "RAGStudyBot"
MAX_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
CHROMA_DIR = Path(os.environ.get("CHROMA_DIR", "./chroma_data"))
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
COLLECTION_NAME = "rag_documents"
CHROMA_COLLECTION_NAME = "rag_documents_chroma"
CHROMA_MODEL = "all-MiniLM-L6-v2"


def normalize_url(url):
    """HTTP(S) URLを検証し、フラグメントを除いて正規化する。"""
    parts = urlsplit(url)
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError(f"HTTP(S) URLを指定してください: {url}")
    # Validate the port before issuing any requests.
    _ = parts.port
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))


@contextmanager
def ingestion_lock():
    """同じ保存先を使う取り込み処理をプロセス間で直列化する。"""
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    with (CHROMA_DIR / "ingestion.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class SafeFetcher:
    """robots.txtとアクセス間隔を確認しながらHTMLを取得する。"""

    def __init__(self):
        """robots.txtのキャッシュとリクエスト用セッションを初期化する。"""
        self.robots = {}
        self.state_path = CHROMA_DIR / "request-times.json"
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def read_state(self):
        """保存済みのホスト別リクエスト時刻と間隔を読み込む。"""
        if self.state_path.exists():
            with self.state_path.open(encoding="utf-8") as file:
                return json.load(file)
        return {}

    def write_state(self, state):
        """ホスト別リクエスト時刻と間隔を保存する。"""
        with self.state_path.open("w", encoding="utf-8") as file:
            json.dump(state, file)

    def request(self, url):
        """アクセス間隔と応答制限を守ってURLにGETリクエストを送る。

        Returns:
            HTTPステータス、ヘッダー、応答本文のタプル。
            リダイレクト時の本文は空。
        """
        host = urlsplit(url).hostname.lower()
        state = self.read_state()
        host_state = state.setdefault(host, {"last": 0, "interval": 1})
        delay = host_state["interval"] - (time.time() - host_state["last"])
        if delay > 0:
            time.sleep(delay)
        host_state["last"] = time.time()
        self.write_state(state)

        with self.session.get(url, allow_redirects=False, stream=True, timeout=(10, 30)) as response:
            if "Retry-After" in response.headers:
                raise ValueError(
                    f"Retry-After: {response.headers['Retry-After']} が指定されたため取得を停止しました: {url}"
                )
            if response.status_code in (401, 403, 429, 503):
                raise ValueError(f"HTTP {response.status_code} のため取得を停止しました: {url}")
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError(f"リダイレクト先がありません: {url}")
                return response.status_code, response.headers, b""
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError(f"応答がサイズ上限を超えました: {url}")
            return response.status_code, response.headers, bytes(body)

    def check_robots(self, url):
        """対象オリジンのrobots.txtを検証し、取得許可と間隔を確認する。"""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            robots_url = f"{origin}/robots.txt"
            status, _, body = self.request(robots_url)
            if 300 <= status < 400:
                raise ValueError(f"robots.txt のリダイレクトは確認できません: {robots_url}")
            text = body.decode("utf-8-sig", errors="strict")
            directives = []
            has_group = False
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if ":" not in line:
                    raise ValueError(f"robots.txt を解釈できません: {robots_url}")
                name, value = line.split(":", 1)
                name, value = name.strip().lower(), value.strip()
                if not name or (name == "user-agent" and not value):
                    raise ValueError(f"robots.txt を解釈できません: {robots_url}")
                if name == "user-agent":
                    has_group = True
                elif name in ("allow", "disallow", "crawl-delay", "crawl delay", "request-rate", "request rate", "visit-time", "visit time") and not has_group:
                    raise ValueError(f"robots.txt のグループを解釈できません: {robots_url}")
                if name in ("visit-time", "visit time"):
                    raise ValueError(f"robots.txt の visit-time はサポートしていません: {robots_url}")
                if name in ("crawl-delay", "crawl delay", "request-rate", "request rate"):
                    rule = Protego.parse(f"User-agent: *\n{name}: {value}\n")
                    parsed = (
                        rule.crawl_delay(USER_AGENT)
                        if name in ("crawl-delay", "crawl delay")
                        else rule.request_rate(USER_AGENT)
                    )
                    if parsed is None:
                        raise ValueError(f"robots.txt の {name} を確認できません: {robots_url}")
                directives.append((name, value))
            if not any(name == "user-agent" and value for name, value in directives):
                raise ValueError(f"robots.txt の User-agent を確認できません: {robots_url}")
            self.robots[origin] = Protego.parse(text)
        delay = self.robots[origin].crawl_delay(USER_AGENT) or 0
        rate = self.robots[origin].request_rate(USER_AGENT)
        if rate is not None:
            if rate.requests < 1 or rate.seconds <= 0:
                raise ValueError(f"robots.txt の request-rate が不正です: {origin}")
            # One request per complete rate window is conservative, including the robots request.
            delay = max(delay, rate.seconds)
        state = self.read_state()
        host_state = state[parts.hostname.lower()]
        host_state["interval"] = max(host_state["interval"], delay, 1)
        self.write_state(state)
        if not self.robots[origin].can_fetch(url, USER_AGENT):
            raise ValueError(f"robots.txt により取得が禁止されています: {url}")

    def fetch(self, url):
        """リダイレクト先も確認し、最終URLとHTMLから抽出したテキストを返す。"""
        url = normalize_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            self.check_robots(url)
            status, headers, body = self.request(url)
            if 300 <= status < 400:
                url = normalize_url(urljoin(url, headers["Location"]))
                continue
            content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError(f"HTML以外のコンテンツは取り込めません: {content_type}")
            soup = BeautifulSoup(body, "html.parser")
            for element in soup(["script", "style", "noscript"]):
                element.decompose()
            text = soup.get_text(separator="\n", strip=True)
            if not text:
                raise ValueError(f"本文を取得できませんでした: {url}")
            return url, text
        raise ValueError("リダイレクト回数の上限を超えました")


class ChromaDefaultEmbeddings(Embeddings):
    """ChromaDB標準の埋め込み関数をLangChain向けに変換する。"""

    def __init__(self):
        """ChromaDB標準の埋め込み関数を用意する。"""
        self.embedding_function = DefaultEmbeddingFunction()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """複数の文書を埋め込みベクトルに変換する。"""
        return [vector.tolist() for vector in self.embedding_function(texts)]

    def embed_query(self, text: str) -> list[float]:
        """検索クエリを埋め込みベクトルに変換する。"""
        return self.embed_documents([text])[0]


def vector_store():
    """設定された埋め込み方式に対応する永続コレクションを開く。

    既存コレクションの埋め込みモデルが設定と異なる場合は拒否する。
    """
    backend = os.environ.get("EMBEDDING_BACKEND", "ollama")
    if backend == "ollama":
        model = EMBEDDING_MODEL
        collection_name = COLLECTION_NAME
        embeddings = OllamaEmbeddings(
            model=model,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        )
    elif backend == "chroma":
        model = CHROMA_MODEL
        collection_name = CHROMA_COLLECTION_NAME
        embeddings = ChromaDefaultEmbeddings()
    else:
        raise ValueError(f"不明なEMBEDDING_BACKENDです: {backend}（ollama または chroma を指定）")

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"embedding_model": model},
        embedding_function=None,
    )
    if not collection.metadata or collection.metadata.get("embedding_model") != model:
        raise ValueError("保存済みデータの埋め込みモデルが現在の設定と一致しません")
    return Chroma(
        collection_name=collection_name,
        client=client,
        embedding_function=embeddings,
    )


def ingest(url, store):
    """URLのテキストを分割して保存し、最終URLと保存件数を返す。

    同じ最終URLの既存チャンクを更新し、不要になったチャンクを削除する。
    """
    source, text = SafeFetcher().fetch(url)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=200,
        separators=["\n\n", "\n", "。", "、", " ", ""],
    )
    chunks = splitter.split_documents([Document(page_content=text, metadata={"source": source})])
    if not chunks:
        raise ValueError("保存するテキストがありません")
    prefix = hashlib.sha256(source.encode("utf-8")).hexdigest()
    ids = [f"{prefix}-{index}" for index in range(len(chunks))]
    previous_ids = set(store.get(where={"source": source}, include=[])["ids"])
    store.add_documents(chunks, ids=ids)
    stale_ids = list(previous_ids - set(ids))
    if stale_ids:
        store.delete(ids=stale_ids)
    return source, len(chunks)


def search(query, store, limit):
    """検索語に類似する文書を最大limit件返す。"""
    if not query.strip():
        raise ValueError("検索語を指定してください")
    if limit < 1:
        raise ValueError("検索件数は1以上にしてください")
    return store.similarity_search(query, k=limit)


def main():
    """コマンドライン引数に従って取り込みまたは検索を実行する。"""
    parser = argparse.ArgumentParser(description="URLの取り込みとChromaDBの検索")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_parser = commands.add_parser("ingest", help="URLを取得して埋め込み")
    ingest_parser.add_argument("url")
    ingest_parser.add_argument(
        "--confirm-permission", action="store_true", required=True,
        help="利用規約・ライセンス等を確認し、保存が許可されていることを確認",
    )
    search_parser = commands.add_parser("search", help="類似文書を検索")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=4)
    args = parser.parse_args()

    try:
        if args.command == "ingest":
            with ingestion_lock():
                source, count = ingest(args.url, vector_store())
            print(f"{source} から {count} 件のチャンクを保存しました。")
        else:
            documents = search(args.query, vector_store(), args.limit)
            if not documents:
                print("検索結果はありません。")
            for index, document in enumerate(documents, 1):
                print(f"[{index}] {document.metadata['source']}\n{document.page_content}\n")
    except (ValueError, OSError, requests.RequestException) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
