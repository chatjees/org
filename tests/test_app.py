"""Offline tests: the Claude client is replaced by a fake, so no API key or network is needed."""

import json
from types import SimpleNamespace

import pytest

import app as server
from chatjees.prompts import MODES, SUBJECTS, build_system_prompt

TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FakeStream:
    def __init__(self, chunks, stop_reason="end_turn"):
        self.text_stream = iter(chunks)
        self._final = SimpleNamespace(stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=10, output_tokens=5))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._final


class FakeClient:
    def __init__(self, chunks=("Hello ", "$x^2$"), stop_reason="end_turn"):
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                return FakeStream(chunks, stop_reason)

        self.beta = SimpleNamespace(messages=Messages())


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


def parse_sse(body: str):
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_index_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"ChatJEEs" in res.data


def test_system_prompt_mentions_subject_and_mode():
    prompt = build_system_prompt("physics", "practice")
    assert "Physics" in prompt and "Practice coach" in prompt
    # unknown values fall back safely
    assert "all JEE subjects" in build_system_prompt("biology", "nope")
    assert set(SUBJECTS) >= {"physics", "chemistry", "maths"} and "doubt" in MODES


def test_chat_streams_and_sends_correct_request(client, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(server, "_client", fake)
    res = client.post("/api/chat", json={
        "subject": "maths", "mode": "doubt",
        "messages": [{"role": "user", "content": "Integrate x"}],
    })
    assert res.status_code == 200
    events = parse_sse(res.get_data(as_text=True))
    assert [e for e, _ in events] == ["delta", "delta", "done"]
    assert "".join(d["text"] for e, d in events if e == "delta") == "Hello $x^2$"

    call = fake.calls[0]
    assert call["model"] == server.MODEL
    assert call["fallbacks"] == "default"
    assert "Mathematics" in call["system"]
    assert call["messages"] == [{"role": "user", "content": "Integrate x"}]


def test_image_only_on_last_turn(client, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(server, "_client", fake)
    client.post("/api/chat", json={
        "subject": "physics", "mode": "doubt",
        "messages": [
            {"role": "user", "content": "old", "image": TINY_PNG},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "new", "image": TINY_PNG},
        ],
    })
    msgs = fake.calls[0]["messages"]
    assert msgs[0]["content"] == "old"  # earlier image dropped
    assert msgs[2]["content"][0]["type"] == "image"
    assert msgs[2]["content"][0]["source"]["media_type"] == "image/png"


def test_refusal_reported(client, monkeypatch):
    monkeypatch.setattr(server, "_client", FakeClient(chunks=(), stop_reason="refusal"))
    res = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    events = parse_sse(res.get_data(as_text=True))
    assert events[0][0] == "error"


@pytest.mark.parametrize("payload", [
    {"messages": []},
    {"messages": [{"role": "assistant", "content": "x"}]},
    {"messages": [{"role": "user", "content": "x"}], "mode": "hack"},
    {"messages": [{"role": "user", "content": "x", "image": "data:text/html;base64,AAAA"}]},
])
def test_bad_input_rejected(client, payload):
    assert client.post("/api/chat", json=payload).status_code == 400


def test_empty_failed_assistant_turn_is_merged(client, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(server, "_client", fake)
    client.post("/api/chat", json={"messages": [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "q2"},
    ]})
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "q1\n\nq2"}]
