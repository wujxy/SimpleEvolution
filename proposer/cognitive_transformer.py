"""One-shot mentor challenge for a Scientist working model."""
from __future__ import annotations

from collections.abc import Collection

from simpleevo.generator import Generator, select_one_generator

from .model import ChatModel


_SYSTEM = """Apply exactly one supplied cognitive operator to the supplied
research working model or episode seed. Preserve objective facts.
Do not generate implementation proposals. Do not declare the source model wrong.
Expose assumptions or boundaries targeted by the operator, offer alternative
framings, and end with questions that distinguish the framings. Return plain
text only."""


class CognitiveTransformer:
    """Apply exactly one generator in one stateless model call."""

    def __init__(
        self,
        *,
        model: ChatModel,
        generators: dict[str, Generator],
        episode_seed: str = "",
        suggested_operator_id: str | None = None,
    ):
        self.model = model
        self.generators = generators
        self.episode_seed = episode_seed
        self.suggested_operator_id = suggested_operator_id

    def transform(
        self,
        source_text: str,
        operator_id: str | None,
        used_operator_ids: Collection[str],
        timeout_seconds: float,
    ) -> tuple[str, str, object]:
        resolved = self._resolve_operator(operator_id, set(used_operator_ids))
        source = (source_text or self.episode_seed).strip()
        if not source:
            raise ValueError("transform_worldview requires a source working model")
        prompt = (
            f"Operator: {resolved.id} — {resolved.name}\n"
            f"Directive: {resolved.description}\n\n"
            f"Source working model or seed:\n{source}"
        )
        reply = self.model.complete(
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            timeout_seconds=timeout_seconds,
        )
        challenge = reply.text.strip()
        if not challenge:
            raise ValueError("cognitive transformation returned an empty challenge")
        return resolved.id, challenge, reply.usage

    def _resolve_operator(
        self,
        operator_id: str | None,
        used_operator_ids: set[str],
    ) -> Generator:
        requested = operator_id or self.suggested_operator_id
        if requested is not None:
            generator = self.generators.get(requested)
            if generator is None:
                raise ValueError(f"unknown generator: {requested}")
            return generator
        generator = select_one_generator(
            list(self.generators.values()), used_operator_ids,
        )
        if generator is None:
            raise ValueError("no unused cognitive generator is available")
        return generator
