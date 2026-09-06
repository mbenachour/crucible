"""Startup assertion: HUNTER.model != VALIDATOR_BUG.model (specs.md §6, §14.3)."""

import pytest

from crucible.llm.registry import ModelEndpoint, ModelRegistry, ModelRole


def _ep(role, model):
    return ModelEndpoint(role=role, model=model, api_base="http://vllm.local")


def test_identical_hunter_and_validator_fails_loudly():
    endpoints = {
        ModelRole.RECON: _ep(ModelRole.RECON, "model-a"),
        ModelRole.HUNTER: _ep(ModelRole.HUNTER, "model-a"),
        ModelRole.VALIDATOR_BUG: _ep(ModelRole.VALIDATOR_BUG, "model-a"),
        ModelRole.VALIDATOR_REACH: _ep(ModelRole.VALIDATOR_REACH, "model-b"),
    }
    with pytest.raises(RuntimeError, match="different models"):
        ModelRegistry(endpoints)


def test_distinct_models_ok_and_sampling_recorded():
    endpoints = {
        ModelRole.RECON: _ep(ModelRole.RECON, "model-a"),
        ModelRole.HUNTER: _ep(ModelRole.HUNTER, "model-a"),
        ModelRole.VALIDATOR_BUG: _ep(ModelRole.VALIDATOR_BUG, "model-b"),
        ModelRole.VALIDATOR_REACH: _ep(ModelRole.VALIDATOR_REACH, "model-c"),
    }
    reg = ModelRegistry(endpoints)
    params = reg.sampling_params(ModelRole.HUNTER)
    assert params["model"] == "model-a" and "temperature" in params
