"""Variation-factor basis loading and sampling."""
from __future__ import annotations

import json
import random

from simpleevo.generator import (
    Generator,
    load_generator_basis,
    sample_generators,
)


def _basis() -> list[Generator]:
    return [
        Generator(id="G1", name="跨域同构移植", description="d1"),
        Generator(id="G2", name="分解", description="d2"),
        Generator(id="G3", name="理想化", description="d3"),
        Generator(id="G4", name="对称提升", description="d4"),
    ]


def test_load_parses_repo_root_basis():
    # The shipped generator.json must parse and contain G1..G10 with
    # non-empty descriptions (they are injected into the Scientist prompt).
    basis = load_generator_basis()
    assert len(basis) == 10
    ids = [g.id for g in basis]
    assert ids == [f"G{i}" for i in range(1, 11)]
    assert all(g.description.strip() for g in basis)


def test_load_missing_file_degrades_to_empty(tmp_path):
    assert load_generator_basis(tmp_path / "nope.json") == []


def test_load_malformed_degrades_to_empty(tmp_path):
    bad = tmp_path / "generator.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_generator_basis(bad) == []


def test_load_skips_invalid_entries(tmp_path):
    path = tmp_path / "generator.json"
    path.write_text(
        json.dumps(
            [
                {"id": "G1", "description": "ok"},
                {"description": "missing id"},
                {"id": "G2"},  # missing description -> empty string
                "not a dict",
            ]
        ),
        encoding="utf-8",
    )
    basis = load_generator_basis(path)
    assert [g.id for g in basis] == ["G1", "G2"]
    assert basis[1].description == ""


def test_sample_respects_tried():
    rng = random.Random(7)
    chosen = sample_generators(_basis(), {"G1", "G2"}, k=2, rng=rng)
    assert {g.id for g in chosen}.isdisjoint({"G1", "G2"})
    assert len(chosen) == 2


def test_sample_no_untried_returns_empty():
    assert sample_generators(_basis(), set(_basis()[i].id for i in range(4))) == []


def test_sample_empty_basis_returns_empty():
    assert sample_generators([], set()) == []


def test_sample_k_truncates_to_untried_count():
    rng = random.Random(3)
    chosen = sample_generators(_basis(), {"G1", "G2", "G3"}, k=2, rng=rng)
    assert [g.id for g in chosen] == ["G4"]


def test_shipped_basis_carries_three_part_standard():
    # Seat design §2.2: every lens is directive + forbidden + self_check —
    # a lens without its negative ban and decidable self-check degrades to
    # decoration (v3 G1 evidence).
    basis = load_generator_basis()
    assert all(item.directive.strip() for item in basis)
    assert all(item.forbidden.strip() for item in basis)
    assert all(item.self_check.strip() for item in basis)
