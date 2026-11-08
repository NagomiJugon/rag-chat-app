# 概要
RAGを利用したAIチャットシステムを作成します。
アルサーガのホームページを題材にRAGシステムをローカル環境で構築し、精度検証・改善まで行います。

# 環境
Docker Engine でローカル環境を構築します。

フロントエンド：React
バックエンド：FastAPI
埋め込み：Ollama（`bge-m3`、既定）またはChromaDBの標準モデル（`all-MiniLM-L6-v2`）
ベクトルDB：ChromaDB（LangChain経由、ローカル永続化）
LLM API：未実装

GPUで推論するには、DockerからNVIDIA GPUを利用できる環境が必要です。DockerではWSL2のGPUサポートを有効にしてください。
プロバイダのAPIも使用手出来るようにします。

## 起動方法

### 1. 事前準備

Docker Engine と Docker Compose プラグインが必要です。インストール済みか確認してください。

```bash
docker --version
docker compose version
```

未インストールの場合は [Docker Engine のインストール手順](https://docs.docker.com/engine/install/) に従ってください。`sudo` なしで実行する場合は、ユーザーを `docker` グループに追加して再ログインします（<https://docs.docker.com/engine/install/linux-postinstall/>）。

バックエンドの依存関係は `backend/requirements.txt` で管理しており、Docker イメージのビルド時に `pip` で自動インストールされます。ホストへの Python のインストールは不要です。

### 2. 起動

`rag-app` ディレクトリで実行します。

Ollamaを使う場合:

```bash
docker compose --profile ollama up --build -d
```

Ollamaを使わずChromaDBの標準埋め込みを使う場合:

```bash
docker compose up --build -d
```

初回はイメージのビルドに数分かかります。起動状態は次で確認できます。

```bash
docker compose ps
curl http://localhost:8000/api/health
```

現在はLLMを使用せず、API は固定の回答を返します。<http://localhost:5173> を開き、メッセージを送信して動作を確認してください。

### 3. URLの取り込みとベクトル検索

取得対象のページと、リダイレクト先を含む各サイトの利用規約・ライセンス・著作権表示を事前に確認し、保存と利用が許可されている場合に限り取り込みます。robots.txt の確認はコマンドが行います。`--confirm-permission` は利用者がそれ以外の条件を確認したことを示す必須オプションです。

#### Ollamaで埋め込む場合（既定）

`--profile ollama` を指定して起動した後、初回のみモデルを取得します。ネットワーク接続と十分なディスク容量が必要です。GPUがなくてもCPUで実行できますが、時間がかかります。

```bash
docker compose exec ollama ollama pull bge-m3
```

```bash
docker compose exec api python rag.py ingest "https://example.com/page" --confirm-permission
docker compose exec api python rag.py search "検索したい内容" --limit 4
```

#### ChromaDBの標準モデルで埋め込む場合

Ollamaの起動は不要です。取り込み・検索の両方で `EMBEDDING_BACKEND=chroma` を指定してください。

```bash
docker compose exec -e EMBEDDING_BACKEND=chroma api python rag.py ingest "https://example.com/page" --confirm-permission
docker compose exec -e EMBEDDING_BACKEND=chroma api python rag.py search "検索したい内容" --limit 4
```

ChromaDBの標準モデル `all-MiniLM-L6-v2` をCPUで実行します。初回の埋め込み時にはChromaDBがモデルをダウンロードするため、ネットワーク接続が必要です。モデルは専用ボリュームにキャッシュされます。このモデルは主に英語向けであり、日本語の検索精度はOllamaの `bge-m3` と異なります。また、入力はモデルの最大256トークンを超えると切り詰められます。

#### 共通の動作

- 指定した単一HTMLページのテキストを分割・埋め込みし、ChromaDBに保存します。リンク先を自動巡回せず、JavaScriptの実行やログインも行いません。
- 検索結果には関連チャンクと、リダイレクト後の取得元URLが表示されます。検索はLLMによる回答生成や既存チャット画面にはまだ接続されていません。
- 同じ最終URLを再取り込みすると、そのURLのチャンクを更新し、不要になった古いチャンクを削除します。更新途中に失敗した場合は同じURLを再実行してください。
- データとモデルはDockerボリュームに保存されるため、通常の `docker compose down` や再ビルド後も残ります。`docker compose down -v` はこれらを含むボリュームを削除します。
- `EMBEDDING_BACKEND` は `ollama`（既定）または `chroma` を指定します。方式ごとに別のコレクションに保存されるため、切り替え後は同じURLをその方式で再取り込みしてください。
- `EMBEDDING_MODEL` でOllamaのモデルを指定できます（`chroma` では使用しません）。利用前に `ollama pull` が必要です。既存のコレクションと異なるモデルは拒否されるため、変更する場合は別のデータ保存先で新規に取り込んでください。`CHROMA_DIR` と `OLLAMA_BASE_URL` も環境変数で変更できます。
- 既定設定の両方式ともローカルで埋め込みを実行し、ページ本文や検索語を外部の埋め込みAPIに送信しません。`OLLAMA_BASE_URL` を変更する場合は接続先にこれらのデータが送られます。

取得処理は各オリジンのrobots.txtを取得して確認し、リダイレクトのたびに移動先も確認します。robots.txtが取得・解釈できない場合、リダイレクトされた場合や、未対応の `Visit-time` が指定されている場合は安全側に停止します。`Retry-After` を受け取った場合も再試行せず停止するため、指定された時間以降に再実行してください。同一のChromaDB保存先を使う取り込みコマンドは直列化され、ホストごとに最低1秒、さらにrobots.txtの `Crawl-delay` や `Request-rate` が指定された場合はより長い間隔を空けます。別の保存先や別のツールから同時にアクセスしないでください。

### 4. 依存関係の更新

`backend/requirements.txt` を編集し、`docker compose up --build -d` で再ビルドします。

## テスト

Pythonの依存関係をインストール済みの環境で、`rag-app` ディレクトリから実行します。外部サイトやOllamaへの接続は不要です。

```bash
python -m unittest discover -s backend -p 'test_*.py' -v
```

ChromaDB標準モデルの実際のダウンロードと推論を含めて検証する場合は、`TEST_CHROMA_MODEL=1 python -m unittest discover -s backend -p 'test_*.py' -v` を実行します。

## エンドポイント

- フロントエンド: <http://localhost:5173>
- FastAPI: <http://localhost:8000>
- API ドキュメント: <http://localhost:8000/docs>
- ヘルスチェック: <http://localhost:8000/api/health>

## 外部サイトからのデータ取得ルール

RAG用データを外部サイトから取得する場合は、アクセスするサイトのルートにある `robots.txt` を取得前に必ず確認し、対象サイトに適用されるルールを遵守してください。`robots.txt` で取得が禁止されているパスにはアクセスしないでください。`robots.txt` を取得・解釈できない場合は取得を開始せず、確認できるまで保留してください。別のUser-Agentへの変更、別経路の利用などで拒否ルールを回避してはいけません。

リダイレクト先が別のホストの場合は、そのホストの `robots.txt` も確認してからアクセスしてください。対象サイトの利用規約、ライセンス、著作権表示も確認し、より厳しい条件に従ってください。必要なページだけを取得し、同一ホストへの同時アクセスは1件、リクエスト間隔は最低1秒とします。`Retry-After` がある場合は必ず従い、401/403では再試行せず、429/503ではアクセスを止めて負荷を避けてください。認証やアクセス制限の回避、個人情報・機微情報の収集、許諾範囲を超える保存・再配布は禁止します。

## 取得したデータの使用範囲
取得したデータは本課題の範囲内でのみ利用し、外部に公開・再配布しない

## 停止方法

```bash
docker compose down
```


## セキュリティ
依存パッケージに既知の脆弱性がないか、更新前とリリース前に確認してください。

```bash
npm audit --prefix frontend
pip-audit -r backend/requirements.txt
```

`pip-audit` は事前にインストールしてください。Docker Scout などを使い、Docker イメージもリリース前に確認してください。監査結果は実行時点の既知情報に基づくもので、安全性を保証するものではありません。