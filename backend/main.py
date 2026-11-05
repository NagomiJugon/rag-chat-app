from fastapi import FastAPI
from pydantic import BaseModel

FIXED_ANSWER = "これは固定の回答です。現在はLLMを使用していません。"

app = FastAPI(title="RAG Chat API")


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    return ChatResponse(response=FIXED_ANSWER)
