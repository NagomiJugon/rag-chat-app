# RAG Chat App

RAGを利用したAIチャットシステムをローカル環境で構築し、精度検証・改善まで行います。

| 項目 | 内容 |
| --- | --- |
| フロントエンド | React（Vite） |
| バックエンド | FastAPI |
| 埋め込み | Ollama（`bge-m3`） |
| ベクトルDB | ChromaDB（LangChain経由、ローカル永続化） |
| LLM API | 未実装（プロバイダのAPIも使用できるようにする予定） |

現在、チャットAPIは固定の回答を返します。

## 必要なもの

- Docker Engine と Docker Compose プラグイン
- GPUで推論する場合は、DockerからNVIDIA GPUを利用できる環境（WSL2ではGPUサポートを有効化）

## 起動と停止

```bash
docker compose up --build -d                     # 起動（初回はビルドに数分かかります）
docker compose exec ollama ollama pull bge-m3    # 初回のみ：埋め込みモデルを取得
docker compose down                              # 停止
```

`backend/requirements.txt` を変更した場合も `docker compose up --build -d` で再ビルドします。

| URL | 内容 |
| --- | --- |
| <http://localhost:5173> | フロントエンド |
| <http://localhost:8000/docs> | APIドキュメント |
| <http://localhost:8000/api/health> | ヘルスチェック |

## URLの取り込みと検索

取り込む前に、対象サイトの利用規約・ライセンス・著作権表示を確認してください（[開発ポリシー](docs/policy.md)）。`--confirm-permission` は確認済みであることを示す必須オプションです。robots.txt の確認はコマンドが行います。

```bash
docker compose exec api python rag.py ingest "https://example.com/page" --confirm-permission
docker compose exec api python rag.py search "検索したい内容" --limit 4
```

動作の詳細と環境変数は [docs/ingestion.md](docs/ingestion.md) を参照してください。

## テスト

Pythonの依存関係をインストールした環境で実行します。外部サイトやOllamaへの接続は不要です。

```bash
python -m unittest discover -s backend -p 'test_*.py' -v
```

## ドキュメント

- [開発ポリシー](docs/policy.md)：データ取得ルール、データの使用範囲、セキュリティ確認
- [取り込みと検索の詳細](docs/ingestion.md)
