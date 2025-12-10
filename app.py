# app.py
"""
Flask web app (runs on port 5001).
This forwards prompts to a local model API assumed to be on port 5000.
Sessions stored in memory.
"""
import os
import time
import uuid
import traceback
from typing import Optional, List, Dict, Tuple

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="templates", static_folder="static")

# === Model config ===
MODEL_HOST = os.environ.get("MODEL_HOST", "http://127.0.0.1:5000")

# Candidate endpoints (we will try these in order; some servers support different shapes)
MODEL_ENDPOINTS = [
    "/v1/chat/completions",   # OpenAI-style chat completions
    "/v1/completions",        # alternative
    "/predict",               # llama.cpp server common endpoint
    "/v1/generate",           # some servers
    "/v1/complete",           # fallback
]
MODEL_URLS = [MODEL_HOST.rstrip("/") + ep for ep in MODEL_ENDPOINTS]

# In-memory session store
sessions: Dict[str, dict] = {}

# -----------------------------
# Session Helpers
# -----------------------------
def create_session(name: Optional[str] = None) -> dict:
    sid = str(uuid.uuid4())
    session = {
        "id": sid,
        "name": name or f"Chat {len(sessions) + 1}",
        "created": time.time(),
        "messages": []
    }
    sessions[sid] = session
    return session


def sort_sessions() -> List[dict]:
    """Return sessions sorted by creation time (oldest first)."""
    return sorted(sessions.values(), key=lambda x: x["created"])


# -----------------------------
# Model Response Parser
# -----------------------------
def extract_text_from_model_response(resp_json: dict) -> Tuple[str, dict]:
    """
    Best-effort extraction of text from many model response shapes.
    """
    try:
        if isinstance(resp_json, dict):
            # choices style (OpenAI-like)
            if "choices" in resp_json and isinstance(resp_json["choices"], list) and len(resp_json["choices"]) > 0:
                choice = resp_json["choices"][0]
                # chat-style: choice.message.content
                if isinstance(choice.get("message"), dict) and "content" in choice["message"]:
                    return choice["message"]["content"], resp_json
                # openai-style text
                if "text" in choice and isinstance(choice["text"], str):
                    return choice["text"], resp_json
                # streaming delta
                if "delta" in choice and isinstance(choice["delta"], dict) and "content" in choice["delta"]:
                    return choice["delta"]["content"], resp_json

            # direct top-level string fields
            for key in ("text", "result", "output_text", "response"):
                if key in resp_json and isinstance(resp_json[key], str):
                    return resp_json[key], resp_json

            # data variant: {"data":[{"text": "..."}]}
            if "data" in resp_json and isinstance(resp_json["data"], list) and len(resp_json["data"]) > 0:
                d0 = resp_json["data"][0]
                for k in ("text", "content"):
                    if k in d0 and isinstance(d0[k], str):
                        return d0[k], resp_json

    except Exception:
        pass

    # fallback to string representation
    return str(resp_json), resp_json


# -----------------------------
# Payload trimming helpers
# -----------------------------
MAX_MESSAGES = 24            # keep at most this many messages in the payload we send to the model
MAX_TOTAL_CHARS = 14_000    # approximate safe total characters for messages payload
MAX_MESSAGE_CHARS = 3_000   # trim any single message to this many characters

def trim_messages(messages: List[dict]) -> List[dict]:
    """
    Trim a list of messages to keep the most recent messages while respecting
    MAX_MESSAGES and MAX_TOTAL_CHARS. Also trim individual messages to MAX_MESSAGE_CHARS.
    Returns a new list (not in-place).
    """
    if not messages:
        return messages

    msgs = messages[-MAX_MESSAGES:]  # keep last N first
    # trim individual message sizes (preserve role tag)
    def trim_msg(m):
        content = m.get("content", "")
        if len(content) > MAX_MESSAGE_CHARS:
            # keep tail (most recent) since context usually matters more recently
            content = content[-MAX_MESSAGE_CHARS:]
        return {"role": m.get("role", "user"), "content": content}

    msgs = [trim_msg(m) for m in msgs]

    # if total chars still too big, drop oldest until under limit
    total_chars = sum(len(m["content"]) for m in msgs)
    while total_chars > MAX_TOTAL_CHARS and len(msgs) > 1:
        # drop the oldest message
        msgs.pop(0)
        total_chars = sum(len(m["content"]) for m in msgs)

    return msgs


# -----------------------------
# Robust Model Caller (patched)
# -----------------------------
def call_model(prompt: str,
               max_tokens: int = 256,
               stop=None,
               temperature: float = 0.2,
               messages=None) -> Tuple[bool, str]:
    """
    Try contacting the model server using a list of candidate endpoints and payload shapes.
    Implements:
      - trimming of message history
      - retries with small exponential backoff on recoverable errors
      - fallback to alternate payload shape ('inputs')
      - robust JSON/text parsing
    Returns (ok, text_or_error_message)
    """
    last_err = None

    # prepare messages payload safely
    safe_messages = None
    if messages:
        # build role/content pairs and trim
        safe_messages = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in messages]
        safe_messages = trim_messages(safe_messages)

    # If messages were not provided, send single user message
    if not safe_messages:
        safe_messages = [{"role": "user", "content": prompt}]

    headers = {"Content-Type": "application/json", "Accept": "application/json"}

    for url in MODEL_URLS:
        # Try up to 3 attempts per endpoint (short backoff)
        for attempt in range(3):
            try:
                # Build typical chat completions payload
                payload = {
                    "model": "local-model",
                    "messages": safe_messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                }

                r = requests.post(url, json=payload, headers=headers, timeout=60)

                # If 200 OK, parse
                if r.status_code == 200:
                    # Try JSON parse, fallback to plain text
                    try:
                        j = r.json()
                        text, raw = extract_text_from_model_response(j)
                        # successful
                        return True, text
                    except Exception:
                        # not JSON — return raw text body
                        return True, r.text

                # If Not Found -> try next endpoint (no heavy backoff)
                if r.status_code == 404:
                    last_err = f"{url} returned 404 Not Found"
                    # try next url without waiting
                    break

                # For recoverable errors, do small backoff and retry
                if r.status_code in (408, 409, 429, 500, 502, 503, 504):
                    last_err = f"{url} returned {r.status_code}: {r.text}"
                    time.sleep(0.6 * (attempt + 1))
                    continue

                # Otherwise try fallback 'inputs' shape (some servers expect this)
                payload_inputs = {"inputs": prompt, "parameters": {"max_new_tokens": max_tokens, "temperature": temperature}}
                r2 = requests.post(url, json=payload_inputs, headers=headers, timeout=60)
                if r2.status_code == 200:
                    try:
                        j2 = r2.json()
                        text2, raw2 = extract_text_from_model_response(j2)
                        return True, text2
                    except Exception:
                        return True, r2.text

                # If r2 also 404 then break (endpoint not supported), otherwise record last_err and maybe retry
                if r2.status_code == 404:
                    last_err = f"{url} (inputs) returned 404"
                    break

                last_err = f"{url} returned {r.status_code}: {r.text}"

            except requests.RequestException as re:
                last_err = str(re)
                # small backoff before retrying
                time.sleep(0.5 * (attempt + 1))
                continue
            except Exception as e:
                last_err = str(e)
                time.sleep(0.5 * (attempt + 1))
                continue

    return False, f"Failed to contact model. Last error: {last_err}"


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    ordered = [
        {"id": s["id"], "name": s["name"], "created": s["created"]}
        for s in sort_sessions()
    ]
    return jsonify({"sessions": ordered})


@app.route("/api/sessions", methods=["POST"])
def api_sessions_create():
    data = request.get_json(silent=True) or {}
    session = create_session(data.get("name"))
    # return session under "session" as frontend expects
    return jsonify({"session": {"id": session["id"], "name": session["name"], "created": session["created"]}})


@app.route("/api/sessions/<sid>", methods=["GET"])
def api_sessions_get(sid):
    if sid not in sessions:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"session": sessions[sid]})


@app.route("/api/sessions/<sid>", methods=["DELETE"])
def api_sessions_delete(sid):
    if sid in sessions:
        del sessions[sid]
        return jsonify({"deleted": True})
    return jsonify({"deleted": False}), 404


@app.route("/api/sessions/<sid>", methods=["PATCH"])
def api_sessions_rename(sid):
    if sid not in sessions:
        return jsonify({"error": "not_found"}), 404

    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "empty_name"}), 400

    sessions[sid]["name"] = name
    return jsonify({"renamed": True, "session": {"id": sid, "name": name}})


@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json(force=True)
        prompt = data.get("prompt") or data.get("message") or ""
        session_id = data.get("session_id")
        max_tokens = int(data.get("max_tokens", 256))

        if not session_id or session_id not in sessions:
            s = create_session()
            session_id = s["id"]

        session = sessions[session_id]

        # record user message
        session["messages"].append({"role": "user", "content": prompt, "time": time.time()})

        # build messages for model (trimmed)
        messages_for_model = [{"role": m["role"], "content": m["content"]} for m in session["messages"]]
        messages_for_model = trim_messages(messages_for_model)

        ok, model_resp = call_model(prompt, max_tokens=max_tokens, messages=messages_for_model)

        assistant_text = model_resp if ok else f"[Model error] {model_resp}"

        # store assistant reply
        session["messages"].append({"role": "assistant", "content": assistant_text, "time": time.time()})

        return jsonify({"session_id": session_id, "response": assistant_text})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/password-check", methods=["POST"])
def api_password_check():
    try:
        data = request.get_json(force=True)
        pw = data.get("password", "")
        score = 0
        suggestions = []
        if not pw:
            return jsonify({"score": 0, "suggestions": ["Empty password"]})
        if len(pw) >= 12:
            score += 4
        if any(c.isupper() for c in pw):
            score += 2
        if any(c.islower() for c in pw):
            score += 1
        if any(c.isdigit() for c in pw):
            score += 2
        if any(not c.isalnum() for c in pw):
            score += 1
        if len(pw) < 12:
            suggestions.append("Use at least 12 characters.")
        if not any(c.isdigit() for c in pw):
            suggestions.append("Add digits.")
        if not any(c.isupper() for c in pw):
            suggestions.append("Add uppercase letters.")
        if not any(not c.isalnum() for c in pw):
            suggestions.append("Add symbols.")
        score = min(10, max(1, int((score / 10) * 10)))
        return jsonify({"score": score, "suggestions": suggestions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan-text", methods=["POST"])
def api_scan_text():
    try:
        data = request.get_json(force=True)
        text = data.get("text", "")
        issues = []
        score = 0
        if "http://" in text or "https://" in text:
            issues.append("URL(s) detected")
            score += 30
        if "urgent" in text.lower() or "immediately" in text.lower():
            issues.append("Urgent language")
            score += 20
        return jsonify({"score": min(100, score), "issues": issues})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/session/clear", methods=["POST"])
def api_session_clear():
    data = request.get_json(force=True)
    sid = data.get("session_id")
    if sid and sid in sessions:
        sessions[sid]["messages"] = []
        return jsonify({"cleared": True})
    return jsonify({"cleared": False}), 404


@app.route("/health")
def health():
    ok, _ = call_model("Hello", max_tokens=5)
    return jsonify({"model_reachable": ok, "sessions_count": len(sessions)})


if __name__ == "__main__":
    print("Flask running on port 5001")
    app.run(host="0.0.0.0", port=5001, debug=True)
