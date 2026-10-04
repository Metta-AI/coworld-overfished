import asyncio
import base64
import json
from uuid import uuid4

import httpx
import pytest

from overfished.lifecycle import OwnershipUnsettled
from overfished.llm import LlmError, Transport
from overfished.trajectory import Attempt


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_actual_native_body_headers_and_private_errors(failure):
    call_id = str(uuid4())
    raw = (
        b"PRIVATE_SENTINEL_PROVIDER_ERROR"
        if failure
        else b' {"model":"actual/served","choices":[{"message":{"content":"{\\"effort\\":0.4}"},'
        b'"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1}} \n'
    )
    received = []

    async def handle(reader, writer):
        headers = {}
        assert (await reader.readline()).startswith(b"POST /v1/chat/completions ")
        while line := await reader.readline():
            if line == b"\r\n":
                break
            name, value = line.decode().split(":", 1)
            headers[name.lower()] = value.strip()
        body = json.loads(await reader.readexactly(int(headers["content-length"])))
        received.append((headers, body))
        status = "429 Too Many Requests" if failure else "200 OK"
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Length: {len(raw)}\r\n"
            f"X-Softmax-Llm-Call-Id: {call_id}\r\nX-Request-Id: actual-provider\r\n\r\n".encode()
            + raw
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    transport = Transport(f"http://127.0.0.1:{port}", 1)
    attempt = Attempt(policy="requested/model", inference_mode="text_action", prompt=[])
    try:
        if failure:
            with pytest.raises(LlmError) as error:
                await transport.complete(
                    model="requested/model", messages=[], max_tokens=64, slot=3, evidence=attempt
                )
            assert "PRIVATE_SENTINEL" not in str(error.value)
        else:
            assert (
                await transport.complete(
                    model="requested/model", messages=[], max_tokens=64, slot=3, evidence=attempt
                )
                == '{"effort":0.4}'
            )
            assert attempt.model == "actual/served"
        assert received[0][0]["x-coworld-player-slot"] == "3"
        assert "authorization" not in received[0][0]
        assert received[0][1]["temperature"] == 1
        assert received[0][1]["top_p"] == 1
        assert attempt.raw_response == raw.decode()
        assert base64.b64decode(attempt.response_body_b64, validate=True) == raw
        assert attempt.http_status == (429 if failure else 200)
        assert attempt.response_complete is True and attempt.response_reader_joined is True
        assert str(attempt.platform_call_id) == call_id
        assert attempt.provider_request_id == "actual-provider"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_absolute_deadline_keeps_partial_invalid_utf8_and_joins_actual_reader():
    started = asyncio.Event()
    finished = asyncio.Event()
    prefix = b'{"private":"ACTUAL_PREFIX\xe2'
    call_id = str(uuid4())

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nX-Softmax-Llm-Call-Id: {call_id}\r\n\r\n".encode()
            + prefix
        )
        await writer.drain()
        started.set()
        while await reader.read(1024):
            pass
        writer.close()
        await writer.wait_closed()
        finished.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    transport = Transport(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", 0.2)
    attempt = Attempt(policy="model", inference_mode="text_action", prompt=[])
    try:
        with pytest.raises(LlmError, match="TimeoutError"):
            await transport.complete(model="model", messages=[], max_tokens=64, slot=0, evidence=attempt)
        assert started.is_set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert base64.b64decode(attempt.response_body_b64, validate=True) == prefix
        assert attempt.raw_response is None
        assert attempt.response_complete is False and attempt.response_reader_joined is True
        assert str(attempt.platform_call_id) == call_id
    finally:
        server.close()
        await server.wait_closed()


class UncancelledFuture(asyncio.Future):
    def cancel(self, msg=None):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("sampling", [True, False])
async def test_long_context_actual_native_sampling_and_greedy_null(sampling, monkeypatch):
    monkeypatch.setenv("COWORLD_LLM_TEMPERATURE", "1" if sampling else "0")
    sample = {
        "policy_revision": "synthetic-model",
        "tokenizer_revision": "synthetic-tokenizer",
        "chat_template": "synthetic-template",
        "sampling": "full_softmax_temperature_one",
        "enable_thinking": False,
        "max_new_tokens": 64,
        "max_sequence_length": 32768 + 64,
        "sampling_seed": 0,
        "eos_token_ids": [4],
        "prompt_token_ids": list(range(32768)),
        "completion_token_ids": [3, 4],
        "behavior_log_probs": [-0.5, -0.6],
        "response": '{"effort":0.4}',
        "stop_reason": "eos",
    }
    raw = json.dumps(
        {
            "model": "actual/checkpoint",
            "choices": [{"message": {"content": sample["response"]}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 32768 if sampling else 9, "completion_tokens": 2},
            "sampling_evidence": sample if sampling else None,
        }
    ).encode()

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    transport = Transport(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", 2)
    attempt = Attempt(policy="model", inference_mode="text_action", prompt=[])
    try:
        assert (
            await transport.complete(model="model", messages=[], max_tokens=64, slot=0, evidence=attempt)
            == sample["response"]
        )
        assert base64.b64decode(attempt.response_body_b64, validate=True) == raw
        assert attempt.response_reader_joined is True
        if sampling:
            assert attempt.prompt_token_ids == sample["prompt_token_ids"]
            assert attempt.sampled_token_ids == [3, 4]
            assert attempt.behavior_logprobs == [-0.5, -0.6]
            assert attempt.stop_reason == "eos"
        else:
            assert attempt.prompt_token_ids is None
            assert attempt.sampled_token_ids is None
            assert attempt.behavior_logprobs is None
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_unsettled_actual_client_close_never_claims_reader_join(monkeypatch):
    pending = UncancelledFuture()
    original = httpx.AsyncClient.aclose
    clients = []

    async def stalled_close(client):
        clients.append(client)
        await pending
        await original(client)

    monkeypatch.setattr(httpx.AsyncClient, "aclose", stalled_close)
    monkeypatch.setattr("overfished.lifecycle.CLEANUP_SECONDS", 0.05)

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 429 Too Many Requests\r\nContent-Length: 3\r\n\r\nraw")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    transport = Transport(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", 1)
    attempt = Attempt(policy="model", inference_mode="text_action", prompt=[])
    before = asyncio.all_tasks()
    try:
        with pytest.raises(OwnershipUnsettled):
            await transport.complete(model="model", messages=[], max_tokens=64, slot=0, evidence=attempt)
        assert attempt.raw_response == "raw" and attempt.response_complete is True
        assert attempt.response_reader_joined is False
        assert pending.done() is False
    finally:
        pending.set_result(None)
        outstanding = asyncio.all_tasks() - before - {asyncio.current_task()}
        if outstanding:
            await asyncio.wait(outstanding, timeout=1)
        for client in clients:
            await original(client)
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_reasoning_only_is_private_auxiliary_never_an_action():
    raw = json.dumps(
        {
            "model": "actual/served",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "", "reasoning": 'PRIVATE_SENTINEL {"effort":0.4}'},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode()

    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    attempt = Attempt(policy="requested/model", inference_mode="text_action", prompt=[])
    try:
        with pytest.raises(LlmError, match="no action text") as error:
            await Transport(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", 1).complete(
                model="requested/model", messages=[], max_tokens=64, slot=0, evidence=attempt
            )
        assert "PRIVATE_SENTINEL" not in str(error.value)
        assert attempt.raw_response == raw.decode()
        assert attempt.response == "" and attempt.parsed_action is None
        assert attempt.response_complete is True and attempt.response_reader_joined is True
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "field,value", [("response_complete", "true"), ("response_reader_joined", 1), ("http_status", "200")]
)
def test_untrusted_received_facts_are_strict_and_validation_hides_private_input(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as error:
        Attempt.model_validate(
            {
                "policy": "fixture",
                "inference_mode": "text_action",
                "prompt": [],
                field: value,
                "response": "PRIVATE_VALIDATION_SENTINEL",
            }
        )
    assert "PRIVATE_VALIDATION_SENTINEL" not in str(error.value)


@pytest.mark.asyncio
async def test_slow_trickle_cannot_extend_absolute_request_deadline():
    released = asyncio.Event()
    transmitted = bytearray(b'{"unfinished":"')

    async def handle(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in headers.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        await reader.readexactly(length)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + transmitted)
        await writer.drain()
        eof = asyncio.create_task(reader.read())
        try:
            for _ in range(100):
                done, _ = await asyncio.wait({eof}, timeout=0.02)
                if done:
                    break
                transmitted.extend(b" ")
                writer.write(b" ")
                await writer.drain()
            assert eof.done()
        finally:
            writer.close()
            await writer.wait_closed()
            released.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    transport = Transport(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}", 0.2)
    attempt = Attempt(policy="fixture", inference_mode="text_action", prompt=[])
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(LlmError, match="TimeoutError"):
            await transport.complete(
                model="fixture/model", messages=[], max_tokens=64, slot=0, evidence=attempt
            )
        assert asyncio.get_running_loop().time() - started < 0.6
        await asyncio.wait_for(released.wait(), 1)
        received = base64.b64decode(attempt.response_body_b64, validate=True)
        assert len(received) > len(b'{"unfinished":"') and transmitted.startswith(received)
        assert attempt.response_complete is False and attempt.response_reader_joined is True
    finally:
        server.close()
        await server.wait_closed()
