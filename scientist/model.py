"""Minimal chat-model boundary for the Proposer runtime.

OpenAI-compatible transports (HEPAI, Zhipu, and plain OpenAI) share the
Chat Completions wire format, so the turn loop lives once on the shared
base; each subclass only owns how its client is built and which secret
environment variable authenticates it.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Protocol


class ModelError(RuntimeError):
    """The configured model transport cannot produce a usable reply."""


def resolve_api_key(config: dict, *env_names: str, provider: str) -> str:
    """Config-pinned key wins; environment is only the fallback.

    A key written into the task yaml (``researcher.api_key``) is the user's
    explicit choice for THIS run and must never be silently overridden by
    whatever credential happens to sit in the submitting shell — a stale
    exported token from another provider era cost a full lane outage once
    too often. Order: ``api_key`` in the role config, then the historical
    environment variables, in the order given.
    """
    key = str(config.get("api_key") or "").strip()
    if key:
        return key
    for name in env_names:
        key = os.environ.get(name)
        if key:
            return key
    raise ModelError(
        f"no API key for the {provider} proposer: set researcher.api_key "
        f"in the task config or export one of {', '.join(env_names)}"
    )


class EmptyReplyError(ModelError):
    """The model answered with zero content bytes.

    For streaming reasoning models this is usually self-healing: the model
    can spend its whole output budget on thinking (content channel stays
    empty), or a gateway can truncate the stream. It is therefore retried
    like any other transient failure — see ``_is_transient``."""


@dataclass(frozen=True)
class ToolCall:
    """One provider-native tool call, arguments kept BOTH ways.

    ``arguments`` is the parsed dict when the provider's JSON parsed (the
    normal case — the provider emits tool arguments as a JSON string); it is
    None when parsing failed, and ``arguments_raw`` then carries the bytes so
    the caller's repair path can quote them back to the model."""

    id: str = ""
    name: str = ""
    arguments: dict | None = None
    arguments_raw: str = ""


@dataclass(frozen=True)
class ModelReply:
    text: str
    usage: object = None
    tool_calls: tuple[ToolCall, ...] = ()
    # The hidden reasoning of a thinking-mode reply. DeepSeek's API
    # requires it to be passed back on replayed assistant turns in
    # multi-turn tool loops ("The reasoning_content in the thinking mode
    # must be passed back"); a real run died at step 32 without it.
    reasoning: str = ""


class ChatModel(Protocol):
    def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        timeout_seconds: float,
        json_object: bool = True,
        tools: list[dict] | None = None,
    ) -> ModelReply:
        """Return one assistant message.

        ``json_object`` opts OUT of the OpenAI-compatible
        ``response_format={"type": "json_object"}`` guard. The Scientist
        protocol needs strict JSON, but consumers that expect free text (the
        cognitive transformer's challenge) must not pay for it: DeepSeek 400s
        json_object calls whose prompt lacks the word 'json', and its
        json_object mode is the fragile one that has returned empty/missing
        content before.

        ``tools`` switches the call to provider-native tool calling: the
        OpenAI-compatible ``tools`` parameter is sent instead of
        ``response_format``, and the reply may carry ``tool_calls``
        (structured calls — no JSON-in-prose protocol to repair). Messages
        may then contain the wire forms ``{"role": "assistant", ...,
        "tool_calls": [...]}`` and ``{"role": "tool", "tool_call_id": ...,
        "content": ...}``; they pass through untouched.
        """


# --- transient-error retry ------------------------------------------------
#
# A flaky upstream gateway (504 / 503 / 502) or a momentary connection drop
# must not burn a whole Scientist round — the very first call dying at nginx
# used to abstain the round outright. The OpenAI SDK retries some of these
# internally, but the HEPAI wrapper bypasses that path, so we run our own
# backoff loop around the single chat-completions call.

# HTTP status codes worth retrying (the gateway/overload/temporary family).
_RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _is_transient(exc: BaseException) -> bool:
    """True for gateway / connection / timeout errors worth retrying.

    ``APIStatusError`` (and ``HAPIStatusError``, which subclasses it) carries a
    clean ``status_code``; connection/timeout errors are detected by class name
    across the openai SDK, httpx, and the HEPAI wrapper. An ``EmptyReplyError``
    is also transient: reasoning models can exhaust their output budget on
    thinking (empty content channel) and gateways can truncate a stream —
    both typically succeed on a fresh call. Non-transient errors
    (400/401/403/404, parse failures) are NOT retried — they
    will not fix themselves."""
    if isinstance(exc, EmptyReplyError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRY_STATUS:
        return True
    mro = " ".join(cls.__name__.lower() for cls in type(exc).__mro__)
    if "timeout" in mro or "connection" in mro:
        return True
    message = str(exc).lower()
    return any(tag in message for tag in (
        "gateway", "bad gateway", "service unavailable", "timed out",
    ))


class _RetryChatModel:
    """Shared deadline-aware retry loop for one-shot model calls.

    Transient upstream failures (504/503/502, connection drops, timeouts)
    are retried with exponential backoff — a flaky gateway must not consume
    a round. Subclasses implement ``_create`` (the provider call) and
    ``_to_reply`` (response -> ModelReply)."""

    def __init__(self, *, max_retries: int = 8, retry_base_delay: float = 2.0):
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    def _retry_delay(self, attempt: int) -> float:
        """Exponential backoff with ±25% jitter, capped at 30s."""
        delay = min(self._retry_base_delay * (2 ** attempt), 30.0)
        return delay * random.uniform(0.75, 1.25)

    def _create(self, *, system: str, messages: list[dict],
                remaining: float, json_object: bool = True,
                tools: list[dict] | None = None):
        raise NotImplementedError

    def _to_reply(self, response) -> ModelReply:
        raise NotImplementedError

    def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        timeout_seconds: float,
        json_object: bool = True,
        tools: list[dict] | None = None,
    ) -> ModelReply:
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError(
                    "model call deadline exceeded"
                    + (f" (last error: {last_exc})" if last_exc else "")
                )
            try:
                # Response CONSUMPTION stays inside the retry try: with
                # streaming, a connection can also die mid-reply (after
                # _create returned a healthy stream), and that death must
                # retry like any other transient failure.
                response = self._create(
                    system=system, messages=messages, remaining=remaining,
                    json_object=json_object, tools=tools,
                )
                return self._to_reply(response)
            except Exception as exc:
                last_exc = exc
                transient = _is_transient(exc)
                if not (transient and attempt < self._max_retries):
                    raise
                # cap the sleep so we never overshoot the call deadline
                delay = min(
                    self._retry_delay(attempt),
                    max(0.0, deadline - time.monotonic() - 1.0),
                )
                if delay <= 0:
                    raise
                print(
                    f"[{time.strftime('%H:%M:%S')}] [model] transient "
                    f"{type(exc).__name__} "
                    f"(status={getattr(exc, 'status_code', '-')}, "
                    f"attempt {attempt + 1}/{self._max_retries}); "
                    f"retrying in {delay:.1f}s",
                    flush=True,
                )
        raise ModelError("unreachable")  # pragma: no cover


class OpenAICompatChatModel(_RetryChatModel):
    """Chat Completions adapter for any OpenAI-compatible endpoint.

    Provider details (auth, base_url, SDK choice) stop at ``from_config``;
    the request/response logic is identical across providers.

    Requests are STREAMED. Reasoning models routinely think for minutes
    before their first output token; with ``stream=False`` the connection
    carries zero bytes the whole time, so any idle-timeout gateway between
    us and the model (HEPAI's cuts at ~300s) drops exactly the
    deepest-thinking calls as 504s — and a blind retry re-pays the whole
    think. Streaming keeps bytes flowing (reasoning deltas arrive every
    few seconds), which both survives the gateway and preserves the
    model's full thinking. Only ``content`` deltas are concatenated;
    reasoning deltas are skipped — the Scientist protocol expects the JSON
    action in the content channel."""

    def __init__(self, *, client, model: str,
                 max_retries: int = 4, retry_base_delay: float = 2.0,
                 reasoning_effort: str | None = None,
                 max_output_tokens: int | None = None):
        super().__init__(
            max_retries=max_retries, retry_base_delay=retry_base_delay,
        )
        self.client = client
        self.model = model
        # Optional thinking-depth valve (config: roles.researcher.
        # reasoning_effort: low|medium|high). None = the provider's
        # server-side default.
        self.reasoning_effort = reasoning_effort
        # Explicit output budget.  Without it the provider default applies
        # — probe A caught it cutting a deliver_world action mid-JSON: the
        # seat's world was DONE and the delivery serialization died five
        # protocol repairs in a row because the model kept re-emitting the
        # same long handover into the same ceiling.  Default generous: a
        # terminal action carries the whole handover in one reply.
        self.max_output_tokens = max_output_tokens or 8192
        # Dropped permanently if the provider rejects stream_options (some
        # OpenAI-compatible gateways don't know it).
        self._stream_usage = True

    def _create(self, *, system: str, messages: list[dict],
                remaining: float, json_object: bool = True,
                tools: list[dict] | None = None):
        kwargs: dict = dict(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
            stream=True,
            timeout=remaining,
        )
        if self.max_output_tokens:
            kwargs["max_tokens"] = self.max_output_tokens
        if tools:
            # Native tool calling: the structured channel replaces the
            # json_object guard entirely (sending both is a 400 on most
            # gateways). The model emits tool_calls instead of JSON prose —
            # the whole JSON-in-prose protocol-repair class disappears.
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        elif json_object:
            # The Scientist protocol needs strict JSON. Free-text consumers
            # (cognitive transformer) pass json_object=False: DeepSeek 400s
            # json_object calls whose prompt lacks 'json', and json mode is
            # its most fragile — never force it where the reply is prose.
            kwargs["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        # Provider-native request-body extras (e.g. GLM's thinking switch);
        # the OpenAI SDK merges extra_body into the JSON body.
        if getattr(self, "extra_body", None):
            kwargs["extra_body"] = self.extra_body
        if self._stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            return self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if (self._stream_usage
                    and getattr(exc, "status_code", None) == 400):
                self._stream_usage = False
                kwargs.pop("stream_options")
                return self.client.chat.completions.create(**kwargs)
            raise

    def _to_reply(self, response) -> ModelReply:
        parts: list[str] = []
        reasoning_parts: list[str] = []
        usage = None
        finish_reason = None
        # Native tool calls arrive as per-index fragments across stream
        # chunks: the first fragment carries id + function name, later ones
        # append argument bytes. Assemble by index, then parse.
        tool_fragments: dict[int, dict] = {}
        for chunk in response:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue  # usage-only final chunk
            finish_reason = getattr(choices[0], "finish_reason", None) \
                or finish_reason
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta else None
            if content:
                parts.append(content)
            rc = getattr(delta, "reasoning_content", None) if delta else None
            if rc:
                reasoning_parts.append(rc)
            if delta is not None:
                for tc in getattr(delta, "tool_calls", None) or ():
                    index = getattr(tc, "index", None)
                    index = 0 if index is None else int(index)
                    slot = tool_fragments.setdefault(
                        index, {"id": "", "name": "", "arguments": ""},
                    )
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    function = getattr(tc, "function", None)
                    if function is None:
                        continue
                    if getattr(function, "name", None):
                        slot["name"] += function.name
                    if getattr(function, "arguments", None):
                        slot["arguments"] += function.arguments
        return _assemble_stream_reply(parts, usage, finish_reason,
                                      tool_fragments,
                                      reasoning="".join(reasoning_parts))


def _assemble_stream_reply(
    parts: list[str], usage, finish_reason, tool_fragments: dict[int, dict],
    reasoning: str = "",
) -> ModelReply:
    """The shared tail of every streaming Chat Completions decoder: join
    content, normalize usage to a dict, assemble tool calls by index, and
    fail loudly on an empty reply (finish_reason/completion_tokens carried
    as evidence). Both the SDK transport and the pure-stdlib transport
    (model_stdlib) reduce to this — one wire contract, one assembly."""
    text = "".join(parts)
    if usage is not None and hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    tool_calls = tuple(
        _tool_call_from_fragment(tool_fragments[index])
        for index in sorted(tool_fragments)
        if tool_fragments[index]["name"]
    )
    if not text.strip() and not tool_calls:
        # Carry the evidence: finish_reason=length points at the
        # thinking budget eating the reply (lower reasoning_effort);
        # a null finish_reason points at a truncated stream.
        completion_tokens = (
            usage.get("completion_tokens") if isinstance(usage, dict)
            else None
        )
        raise EmptyReplyError(
            "chat model returned an empty assistant message "
            f"(finish_reason={finish_reason}, "
            f"completion_tokens={completion_tokens})"
        )
    return ModelReply(text=text, usage=usage, tool_calls=tool_calls,
                      reasoning=reasoning)


def _tool_call_from_fragment(slot: dict) -> ToolCall:
    """Assemble one ToolCall from its accumulated stream fragments.

    Arguments arrive as a JSON STRING on the wire; parse to a dict so the
    loop gets structured data (the entire point of the native channel). A
    parse failure keeps ``arguments=None`` with the raw bytes — the caller
    decides whether to repair or reject; silently guessing a schema here
    would hide provider bugs."""
    raw = slot["arguments"]
    try:
        arguments = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        arguments = None
    if not isinstance(arguments, dict):
        arguments = None
    return ToolCall(
        id=slot["id"], name=slot["name"],
        arguments=arguments, arguments_raw=raw,
    )


_EFFORT_LEVELS = ("low", "medium", "high")


def _validated_effort(config: dict) -> str | None:
    """The optional thinking-depth valve from the role config
    (``reasoning_effort: low|medium|high``). None = provider default.
    Fails fast on a typo so a bad value surfaces at startup, not mid-round."""
    value = config.get("reasoning_effort")
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip().lower()
    if value not in _EFFORT_LEVELS:
        raise ModelError(
            f"researcher.reasoning_effort must be one of "
            f"{list(_EFFORT_LEVELS)}; got {value!r}"
        )
    return value


class HepAIChatModel(OpenAICompatChatModel):
    """HEPAI (IHEP) Chat Completions adapter; provider details stop here."""

    @classmethod
    def from_config(cls, config: dict) -> "HepAIChatModel":
        key = resolve_api_key(config, "HEPAI_API_KEY", provider="hepai")
        try:
            from hepai import HepAI
        except ImportError as exc:
            raise ModelError("install the project dependency 'hepai'") from exc
        return cls(
            client=HepAI(api_key=key, base_url=config["base_url"]),
            model=config["model"],
            reasoning_effort=_validated_effort(config),
        )


class ZhipuChatModel(OpenAICompatChatModel):
    """Zhipu (智谱) GLM Chat Completions adapter; provider details stop here.

    Zhipu exposes an OpenAI-compatible endpoint, so we drive it with the
    OpenAI SDK against ``https://open.bigmodel.cn/api/paas/v4/`` rather
    than the Anthropic-compatible ``/api/anthropic`` path (which serves the
    Messages API, not Chat Completions). GLM-4+ models honour
    ``response_format={"type": "json_object"}``.

    GLM's thinking knob is not graded (no low/medium/high) — it is
    ``thinking: {"type": "enabled"|"disabled"}`` in the request body. The
    shared ``reasoning_effort`` config is translated here: low → disabled,
    medium/high → enabled, carried via the SDK's ``extra_body``.
    """

    @classmethod
    def from_config(cls, config: dict) -> "ZhipuChatModel":
        key = resolve_api_key(
            config, "ZHIPU_API_KEY", "ZHIPUAI_API_KEY", provider="zhipu",
        )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ModelError(
                "install the project dependency 'openai'"
            ) from exc
        model = cls(
            client=OpenAI(api_key=key, base_url=config["base_url"]),
            model=config["model"],
        )
        effort = _validated_effort(config)
        if effort:
            model.extra_body = {
                "thinking": {"type": "disabled" if effort == "low"
                             else "enabled"},
            }
        return model


class OpenAIChatModel(OpenAICompatChatModel):
    """Standard OpenAI Chat Completions adapter.

    Drives the OpenAI SDK against the configured ``base_url``; the key comes
    from ``OPENAI_API_KEY``. Used for OpenAI-compatible gateways (e.g. the
    IHEP ``aiapi`` endpoint) that speak the plain Chat Completions wire
    format without a provider-specific wrapper.
    """

    @classmethod
    def from_config(cls, config: dict) -> "OpenAIChatModel":
        key = resolve_api_key(config, "OPENAI_API_KEY", provider="openai")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ModelError(
                "install the project dependency 'openai'"
            ) from exc
        return cls(
            client=OpenAI(api_key=key, base_url=config["base_url"]),
            model=config["model"],
            reasoning_effort=_validated_effort(config),
            max_output_tokens=config.get("max_output_tokens"),
        )


class AnthropicChatModel(_RetryChatModel):
    """Anthropic Messages adapter — for providers that ONLY expose the
    Messages API (e.g. bigmodel's ``/api/anthropic`` channel, whose key is
    not provisioned for the ``/api/paas/v4`` Chat Completions channel).

    Replies concatenate the ``text`` content blocks; ``thinking`` blocks
    (GLM emits them) are skipped — the Scientist protocol expects the JSON
    action in the text channel."""

    def __init__(self, *, client, model: str,
                 max_retries: int = 4, retry_base_delay: float = 2.0):
        super().__init__(
            max_retries=max_retries, retry_base_delay=retry_base_delay,
        )
        self.client = client
        self.model = model

    @classmethod
    def from_config(cls, config: dict) -> "AnthropicChatModel":
        key = resolve_api_key(
            config,
            "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
            provider="anthropic",
        )
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ModelError(
                "install the project dependency 'anthropic'"
            ) from exc
        return cls(
            client=Anthropic(api_key=key, base_url=config["base_url"]),
            model=config["model"],
        )

    def _create(self, *, system: str, messages: list[dict],
                remaining: float, json_object: bool = True,
                tools: list[dict] | None = None):
        # The Messages API has no json_object response mode; the flag is
        # accepted for interface symmetry and ignored. Native tool calling
        # is NOT implemented on this transport — fail fast rather than
        # silently degrading to prose where a caller expects structure.
        if tools:
            raise ModelError(
                "native tool calls are not supported on the "
                "anthropic transport"
            )
        return self.client.messages.create(
            model=self.model,
            system=system,
            messages=messages,
            max_tokens=8192,
            timeout=remaining,
        )

    def _to_reply(self, response) -> ModelReply:
        text = "".join(
            block.text
            for block in (response.content or [])
            if getattr(block, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        if not text.strip():
            # stop_reason=max_tokens means thinking ate the reply budget.
            raise EmptyReplyError(
                "chat model returned an empty assistant message "
                f"(stop_reason={getattr(response, 'stop_reason', None)})"
            )
        return ModelReply(text=text, usage=usage)


def build_chat_model(config: dict) -> ChatModel:
    """Construct the proposer chat model for the configured ``api`` provider.

    A missing/empty ``api`` falls back to ``'openai'``; resolved configs
    always carry an explicit api, so this default only matters for direct
    callers.
    """
    api = (config.get("api") or "openai").strip().lower()
    if api == "hepai":
        return HepAIChatModel.from_config(config)
    if api == "openai":
        return OpenAIChatModel.from_config(config)
    if api == "zhipu":
        return ZhipuChatModel.from_config(config)
    if api == "anthropic":
        return AnthropicChatModel.from_config(config)
    raise ModelError(
        f"researcher.api: unsupported provider {api!r} "
        "(supported: 'hepai', 'openai', 'zhipu', 'anthropic')"
    )
