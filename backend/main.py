"""ヘルスチェックと固定応答のチャットAPIを提供する。"""

from fastapi import FastAPI
from pydantic import BaseModel

FIXED_ANSWER = "これは固定の回答です。現在はLLMを使用していません。"

app = FastAPI(title="RAG Chat API")


class ChatRequest(BaseModel):
    """チャットAPIに送信するメッセージ。"""

    message: str


class ChatResponse(BaseModel):
    """チャットAPIが返す応答。"""

    response: str


@app.get("/api/health")
async def health():
    """APIの稼働状態を返す。"""
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """メッセージを受け取り、現段階では固定の回答を返す。"""
    return ChatResponse(response=FIXED_ANSWER)
