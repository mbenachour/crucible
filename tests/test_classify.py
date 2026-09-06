"""Response classification, including error-text-in-200 (specs.md §12 unit tests)."""

from crucible.llm.classify import ResponseClass, classify


def test_empty_body_is_transient():
    assert classify("") is ResponseClass.TRANSIENT_ERROR


def test_error_text_inside_200_is_transient():
    body = '{"ok": false} upstream error: gateway timeout'
    assert classify(body) is ResponseClass.TRANSIENT_ERROR


def test_refusal_detected():
    assert classify("I can't help with that request.") is ResponseClass.REFUSAL


def test_refusal_not_silently_retried_as_ok():
    # A reframed-but-declining response must still classify as refusal.
    assert classify("I am unable to assist with exploit development.") is ResponseClass.REFUSAL


def test_valid_json_is_ok():
    assert classify('{"verdict": "upheld", "reasoning": "x"}') is ResponseClass.OK


def test_unparseable_despite_constraints_is_malformed():
    assert classify("here is your answer: the bug is real") is ResponseClass.MALFORMED


def test_fenced_json_is_ok():
    assert classify('```json\n{"a": 1}\n```') is ResponseClass.OK
