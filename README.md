# 概要
RAGを利用したAIチャットシステムを作成します。
アルサーガのホームページを題材にRAGシステムをローカル環境で構築し、精度検証・改善まで行います。

# 環境
Docker Engine でローカル環境を構築します。

フロントエンド：React
バックエンド：FastAPI
LLM API：Ollama（`未定`） or プロバイダAPI

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

リポジトリのルートで実行します。

```bash
docker compose up --build -d
```

初回はイメージのビルドに数分かかります。起動状態は次で確認できます。

```bash
docker compose ps
curl http://localhost:8000/api/health
```

現在はLLMを使用せず、API は固定の回答を返します。<http://localhost:5173> を開き、メッセージを送信して動作を確認してください。

### 3. 依存関係の更新

`backend/requirements.txt` を編集し、`docker compose up --build -d` で再ビルドします。

## エンドポイント

- フロントエンド: <http://localhost:5173>
- FastAPI: <http://localhost:8000>
- API ドキュメント: <http://localhost:8000/docs>
- ヘルスチェック: <http://localhost:8000/api/health>

## 外部サイトからのデータ取得ルール

RAG用データを外部サイトから取得する場合は、アクセスするサイトのルートにある `robots.txt` を取得前に必ず確認し、対象サイトに適用されるルールを遵守してください。`robots.txt` で取得が禁止されているパスにはアクセスしないでください。`robots.txt` を取得・解釈できない場合は取得を開始せず、確認できるまで保留してください。別のUser-Agentへの変更、別経路の利用などで拒否ルールを回避してはいけません。

リダイレクト先が別のホストの場合は、そのホストの `robots.txt` も確認してからアクセスしてください。対象サイトの利用規約、ライセンス、著作権表示も確認し、より厳しい条件に従ってください。必要なページだけを取得し、同一ホストへの同時アクセスは1件、リクエスト間隔は最低1秒とします。`Retry-After` がある場合は必ず従い、401/403では再試行せず、429/503ではアクセスを止めて負荷を避けてください。認証やアクセス制限の回避、個人情報・機微情報の収集、許諾範囲を超える保存・再配布は禁止します。

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