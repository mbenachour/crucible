"""Store: saved host-default per-role model config (Settings tab, issue #80)."""

from __future__ import annotations

from crucible.store.dao import Store


def _store(tmp_path):
    return Store(f"sqlite:///{tmp_path}/f.sqlite")


def test_empty_by_default(tmp_path):
    assert _store(tmp_path).get_host_model_config() == {}


def test_set_then_get_roundtrips(tmp_path):
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    assert store.get_host_model_config() == {"hunter": {"model": "deepseek/deepseek-r1-0528"}}


def test_set_is_a_partial_upsert_not_a_replace(tmp_path):
    """Setting `model` then, separately, `temperature` for the same role keeps
    both — matches the partial-endpoint semantics `apply_model_override` and
    a config file's `models:` block already use."""
    store = _store(tmp_path)
    store.set_host_model_config("recon", {"model": "qwen/qwen3-32b"})
    store.set_host_model_config("recon", {"temperature": 0.5})
    assert store.get_host_model_config()["recon"] == {
        "model": "qwen/qwen3-32b", "temperature": 0.5,
    }


def test_set_overwrites_a_field_it_touches(tmp_path):
    store = _store(tmp_path)
    store.set_host_model_config("recon", {"model": "qwen/qwen3-32b"})
    store.set_host_model_config("recon", {"model": "deepseek/deepseek-v4-pro"})
    assert store.get_host_model_config()["recon"]["model"] == "deepseek/deepseek-v4-pro"


def test_clear_removes_the_role_entirely(tmp_path):
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    store.clear_host_model_config("hunter")
    assert store.get_host_model_config() == {}


def test_clear_of_unset_role_is_a_noop(tmp_path):
    store = _store(tmp_path)
    store.clear_host_model_config("hunter")  # never set — must not raise
    assert store.get_host_model_config() == {}


def test_multiple_roles_are_independent(tmp_path):
    store = _store(tmp_path)
    store.set_host_model_config("hunter", {"model": "deepseek/deepseek-r1-0528"})
    store.set_host_model_config("validator_bug", {"model": "qwen/qwen3-32b", "temperature": 0.2})
    got = store.get_host_model_config()
    assert got == {
        "hunter": {"model": "deepseek/deepseek-r1-0528"},
        "validator_bug": {"model": "qwen/qwen3-32b", "temperature": 0.2},
    }
    store.clear_host_model_config("hunter")
    assert store.get_host_model_config() == {
        "validator_bug": {"model": "qwen/qwen3-32b", "temperature": 0.2},
    }
