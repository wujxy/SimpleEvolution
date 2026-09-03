"""Numerical anchors for the trace-mode dielectric boundary model."""

import numpy as np

from benchmarks.JunoResBench.world_generator.authoritative.juno_res_bench.boundary_optics import (
    dielectric_step,
    fresnel_unpolarized,
    medium_group_index,
    medium_refractive_index,
)


def test_normal_incidence_fresnel_and_probability_conservation():
    n1, n2 = 1.49, 1.34
    reflectance, cos_t, tir = fresnel_unpolarized(
        np.array([1.0]), np.array([n1]), np.array([n2])
    )
    expected = ((n1 - n2) / (n1 + n2)) ** 2
    assert np.allclose(reflectance, expected)
    assert np.allclose(cos_t, 1.0)
    assert not tir[0]
    assert np.all((reflectance >= 0.0) & (reflectance <= 1.0))


def test_snell_refraction_preserves_unit_vectors():
    theta_i = np.radians(30.0)
    direction = np.array([[np.sin(theta_i), 0.0, np.cos(theta_i)]])
    normal = np.array([[0.0, 0.0, 1.0]])
    out, reflected = dielectric_step(
        direction, normal, np.array([1.49]), np.array([1.34]),
        np.array([1.0]),
    )
    theta_t = np.arccos(out[0, 2])
    assert not reflected[0]
    assert np.allclose(1.49 * np.sin(theta_i), 1.34 * np.sin(theta_t))
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0)


def test_total_internal_reflection_obeys_specular_law():
    theta_i = np.radians(70.0)
    direction = np.array([[np.sin(theta_i), 0.0, np.cos(theta_i)]])
    normal = np.array([[0.0, 0.0, 1.0]])
    out, reflected = dielectric_step(
        direction, normal, np.array([1.49]), np.array([1.00]),
        np.array([1.0]),
    )
    assert reflected[0]
    assert np.allclose(out, [[np.sin(theta_i), 0.0, -np.cos(theta_i)]])


def test_three_media_have_distinct_dispersion_and_group_indices():
    lam = np.array([350.0, 430.0, 500.0])
    values = [medium_refractive_index(name, lam)
              for name in ("ls", "acrylic", "water")]
    assert np.all(values[0] > values[2])
    assert not np.array_equal(values[0], values[1])
    for name, phase in zip(("ls", "acrylic", "water"), values):
        group = medium_group_index(name, lam)
        assert np.all(group > phase)
        assert group[0] > group[-1]
