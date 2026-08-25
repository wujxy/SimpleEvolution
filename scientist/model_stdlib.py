"""Pure-stdlib streaming Chat Completions transport.

The world container ships a bare python (3.9, no pip, no SDKs) — the
scientist package must close into it whole, so its brain travels over
``urllib`` + hand-parsed SSE instead of the openai SDK. The wire contract
is identical (same body, same ``stream=true`` chunk grammar), so reply
assembly reduces to the same ``_assemble_stream_reply`` the SDK transport
uses; only the HTTP plumbing and the error shapes differ here.

Everything the retry skeleton needs is preserved:
- ``HTTPError`` is re-raised as ``_HttpStatusError`` carrying
  ``status_code`` so ``_is_transient`` retries the gateway family;
- connection failures are raised with the word "connection" in the
  message (``URLError``'s class name alone is not matched);
- socket timeouts surface as ``socket.timeout`` whose MRO name already
  matches;
- a 400 that arrives with ``stream_options`` present is retried once
  without it (gateways that don't know the field), mirroring the SDK
  transport's degradation.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request

from .model import (
    ModelError,
    ModelReply,
    _RetryChatModel,
    _assemble_stream_reply,
    _validated_effort,
)


class _HttpStatusError(ModelError):
    """An HTTP error from the model endpoint, ``status_code`` attached."""

    def __init__(self, status_code: int, exc: urllib.error.HTTPError):
        try:
            detail = exc.read(400).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — best-effort error body
            detail = ""
        message = f"HTTP {status_code} from model endpoint"
        if detail:
            message += f": {detail}"
        super().__init__(message)
        self.status_code = status_code


class StdlibChatModel(_RetryChatModel):
    """Chat Completions over ``urllib`` with SSE line parsing.

    Streams for the same reason the SDK transport does: reasoning models
    think for minutes before their first output token, and a silent
    connection is exactly what an idle-timeout gateway cuts. Usage
    accounting rides the ``stream_options.include_usage`` final chunk.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        max_retries: int = 4,
        retry_base_delay: float = 2.0,
        reasoning_effort: str | None = None,
        max_output_tokens: int | None = None,
    ):
        super().__init__(
            max_retries=max_retries, retry_base_delay=retry_base_delay,
        )
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.url = f"{self.base_url}/chat/completions"
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens or 8192
        # Dropped permanently if the provider rejects stream_options.
        self._stream_usage = True

    @classmethod
    def from_config(cls, config: dict) -> "StdlibChatModel":
        base_url = str(
            config.get("base_url")
            or os.environ.get("OPENAI_BASE_URL") or ""
        ).strip()
        if not base_url:
            raise ModelError(
                "model.base_url is required (or export OPENAI_BASE_URL)"
            )
        key = str(
            config.get("api_key")
            or os.environ.get("OPENAI_API_KEY") or ""
        ).strip()
        if not key:
            raise ModelError(
                "no API key for the model: set model.api_key in the spec "
                "or export OPENAI_API_KEY"
            )
        model_name = str(config.get("model") or "").strip()
        if not model_name:
            raise ModelError("model.model is required")
        return cls(
            model=model_name,
            base_url=base_url,
            api_key=key,
            reasoning_effort=_validated_effort(config),
            max_output_tokens=config.get("max_output_tokens"),
        )

    def _request_body(
        self, *, system: str, messages: list[dict], json_object: bool,
        tools: list[dict] | None,
    ) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            "max_tokens": self.max_output_tokens,
        }
        if tools:
            # Native tool calling replaces the json_object guard entirely.
            body["tools"] = tools
            body["tool_choice"] = "auto"
        elif json_object:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if self._stream_usage:
            body["stream_options"] = {"include_usage": True}
        return body

    def _create(self, *, system: str, messages: list[dict],
                remaining: float, json_object: bool = True,
                tools: list[dict] | None = None):
        body = self._request_body(
            system=system, messages=messages,
            json_object=json_object, tools=tools,
        )
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            return urllib.request.urlopen(
                request, timeout=max(1.0, remaining),
            )
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status == 400 and self._stream_usage:
                # The gateway may not know stream_options — degrade and
                # retry the request once without it.
                self._stream_usage = False
                body.pop("stream_options", None)
                request = urllib.request.Request(
                    self.url,
                    data=json.dumps(body, ensure_ascii=False).encode(
                        "utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )
                try:
                    return urllib.request.urlopen(
                        request, timeout=max(1.0, remaining),
                    )
                except urllib.error.HTTPError as exc2:
                    raise _HttpStatusError(int(exc2.code), exc2) from exc2
                except urllib.error.URLError as exc2:
                    raise ModelError(
                        f"connection failure: {exc2.reason}") from exc2
            raise _HttpStatusError(status, exc) from exc
        except urllib.error.URLError as exc:
            # Keep "connection" in the message: _is_transient matches the
            # word because URLError's own class name says nothing.
            raise ModelError(f"connection failure: {exc.reason}") from exc

    def _to_reply(self, response) -> ModelReply:
        parts: list[str] = []
        usage = None
        finish_reason = None
        tool_fragments: dict[int, dict] = {}
        try:
            for raw_line in response:
                line = (
                    raw_line.decode("utf-8", "replace")
                    if isinstance(raw_line, bytes) else str(raw_line)
                ).strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue  # keep-alive noise / partial line
                if not isinstance(chunk, dict):
                    continue
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue  # usage-only final chunk
                choice = choices[0] if isinstance(choices[0], dict) else {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if content:
                    parts.append(content)
                for tc in delta.get("tool_calls") or ():
                    if not isinstance(tc, dict):
                        continue
                    index = tc.get("index")
                    index = 0 if index is None else int(index)
                    slot = tool_fragments.setdefault(
                        index, {"id": "", "name": "", "arguments": ""},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    function = tc.get("function")
                    if not isinstance(function, dict):
                        continue
                    if function.get("name"):
                        slot["name"] += function["name"]
                    if function.get("arguments"):
                        slot["arguments"] += function["arguments"]
        except http.client.HTTPException as exc:
            # A stream dying mid-reply must retry like any transient
            # failure — IncompleteRead's class name would not match.
            raise ModelError(f"connection failure mid-stream: {exc}") from exc
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001 — cleanup is best-effort
                pass
        return _assemble_stream_reply(parts, usage, finish_reason,
                                      tool_fragments)


def build_stdlib_chat_model(config: dict) -> StdlibChatModel:
    """Construct the package's model from a spec ``model`` block.

    The spec block is trusted (written by the harness or the user's own
    spec.json); env variables are only the fallback for key and base_url.
    """
    return StdlibChatModel.from_config(config)
