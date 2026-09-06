"""Planted bug py-deser-1: unsafe deserialization of an untrusted cache blob."""

import pickle


def load_entry(blob: bytes):
    # BUG(py-deser-1): `blob` is fetched from a shared cache any client can write.
    return pickle.loads(blob)


def get(cache, key: str):
    return load_entry(cache.get_raw(key))
