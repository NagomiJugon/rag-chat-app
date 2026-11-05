import { useEffect, useRef, useState } from "react";

export default function App() {
  const [message, setMessage] = useState("");
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function sendMessage(event) {
    event.preventDefault();
    const text = message.trim();
    if (!text) return;

    setMessages((current) => [...current, { role: "user", text }]);
    setMessage("");
    setLoading(true);
    setError("");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.detail ?? "リクエストに失敗しました。");
      }
      setMessages((current) => [...current, { role: "bot", text: result.response }]);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="chat">
      <header className="chat-header">RAG Chat</header>
      <section className="chat-log" aria-live="polite">
        {messages.map((item, index) => (
          <div key={index} className={`row ${item.role}`}>
            <p className="bubble">{item.text}</p>
          </div>
        ))}
        {loading && (
          <div className="row bot">
            <p className="bubble">...</p>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <div ref={bottomRef} />
      </section>
      <form className="chat-form" onSubmit={sendMessage}>
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          rows="2"
          placeholder="メッセージを入力"
          aria-label="メッセージ"
        />
        <button type="submit" disabled={loading || !message.trim()}>
          送信
        </button>
      </form>
    </main>
  );
}
