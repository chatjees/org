"""ChatJEEs web server.

Run:  ANTHROPIC_API_KEY=... python app.py   then open http://localhost:5000
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator

import anthropic
from flask import Flask, Response, jsonify, render_template, request

from chatjees.prompts import MODES, SUBJECTS, build_system_prompt

MODEL = os.environ.get("CHATJEES_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CHATJEES_EFFORT", "high")  # low | medium | high | xhigh | max
MAX_TOKENS = int(os.environ.get("CHATJEES_MAX_TOKENS", "64000"))
MAX_TURNS = 40  # messages kept from the browser-side history
MAX_TEXT_CHARS = 8000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DATA_URL_RE = re.compile(r"^data:(image/[a-z+]+);base64,([A-Za-z0-9+/=]+)$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


class BadInput(ValueError):
    pass


def _image_block(data_url: str) -> dict[str, Any]:
    match = DATA_URL_RE.match(data_url.strip())
    if not match:
        raise BadInput("Image must be a base64 data URL.")
    media_type, data = match.groups()
    if media_type not in ALLOWED_IMAGE_TYPES:
        raise BadInput(f"Unsupported image type {media_type}.")
    if len(data) * 3 // 4 > MAX_IMAGE_BYTES:
        raise BadInput("Image is larger than 5 MB.")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def build_messages(raw: Any) -> list[dict[str, Any]]:
    """Validate the browser history and convert it to Messages API format.

    Only the newest user turn may carry an image; earlier turns are sent as text so
    the request stays small.
    """
    if not isinstance(raw, list) or not raw:
        raise BadInput("messages must be a non-empty list.")
    raw = raw[-MAX_TURNS:]
    while raw and raw[0].get("role") != "user":  # history must start with a user turn
        raw = raw[1:]
    if not raw or raw[-1].get("role") != "user":
        raise BadInput("The last message must come from the user.")

    messages: list[dict[str, Any]] = []
    for i, msg in enumerate(raw):
        role = msg.get("role")
        text = str(msg.get("content") or "").strip()[:MAX_TEXT_CHARS]
        if role not in ("user", "assistant"):
            raise BadInput("role must be 'user' or 'assistant'.")
        is_last = i == len(raw) - 1
        image = msg.get("image") if is_last else None
        if not text and not image:
            if role == "assistant":
                continue  # skip empty/failed assistant turns
            text = "(image attached)" if image else ""
            if not text:
                raise BadInput("Empty message.")
        if image:
            content: Any = [_image_block(image), {"type": "text", "text": text or "Solve this question."}]
        else:
            content = text
        # merge consecutive same-role turns (e.g. after a skipped failed reply)
        if messages and messages[-1]["role"] == role and isinstance(content, str) and isinstance(messages[-1]["content"], str):
            messages[-1]["content"] += "\n\n" + content
        else:
            messages.append({"role": role, "content": content})
    return messages


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def stream_reply(system: str, messages: list[dict[str, Any]]) -> Iterator[str]:
    try:
        with get_client().beta.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            output_config={"effort": EFFORT},
            # If a safety classifier declines, let the API retry on its recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for text in stream.text_stream:
                yield sse("delta", {"text": text})
            final = stream.get_final_message()

        if final.stop_reason == "refusal":
            yield sse("error", {"message": "This request was declined. Please rephrase your question."})
        elif final.stop_reason == "max_tokens":
            yield sse("notice", {"message": "The answer hit the length limit and was cut short. Ask me to continue."})
        yield sse("done", {"input_tokens": final.usage.input_tokens, "output_tokens": final.usage.output_tokens})
    except anthropic.AuthenticationError:
        yield sse("error", {"message": "Server API key is missing or invalid (set ANTHROPIC_API_KEY)."})
    except anthropic.RateLimitError:
        yield sse("error", {"message": "Too many requests right now. Please wait a minute and retry."})
    except anthropic.BadRequestError as e:
        app.logger.warning("Bad request to Claude API: %s", e.message)
        yield sse("error", {"message": "The request could not be processed (check the image or shorten the chat)."})
    except anthropic.APIStatusError as e:
        app.logger.error("Claude API error %s: %s", e.status_code, e.message)
        yield sse("error", {"message": "The AI service had a problem. Please retry."})
    except anthropic.APIConnectionError:
        yield sse("error", {"message": "Could not reach the AI service. Check the server's network."})


@app.get("/")
def index() -> str:
    return render_template("index.html", subjects=SUBJECTS, modes=list(MODES))


@app.get("/api/health")
def health() -> Response:
    return jsonify(status="ok", model=MODEL)


@app.post("/api/chat")
def chat() -> Response:
    body = request.get_json(silent=True) or {}
    subject = body.get("subject", "general")
    mode = body.get("mode", "doubt")
    if subject not in SUBJECTS or mode not in MODES:
        return jsonify(error="Unknown subject or mode."), 400
    try:
        messages = build_messages(body.get("messages"))
    except BadInput as e:
        return jsonify(error=str(e)), 400

    system = build_system_prompt(subject, mode)
    return Response(
        stream_reply(system, messages),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
