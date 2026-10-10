"""Negative cases keep structural checks from masquerading as factual verification."""

from copy import deepcopy

import pytest

from evaluation.enrichment_check import check_candidate


def _candidate():
    # Deliberately synthetic evidence: it must never receive a factual pass.
    return {
        "outcome": "proposal",
        "fields": {
            name: {
                "value": value,
                "evidence": {"source_url": "https://example.org/test.pdf", "page": 1, "excerpt": "Synthetic test evidence"},
            }
            for name, value in {
                "manufacturer": "Nexperia", "part_number": "PBSS5350T",
                "package": "SOT-23", "description": "PNP transistor with low saturation voltage",
            }.items()
        },
    }


def test_plausible_values_and_even_fabricated_evidence_still_require_review():
    result = check_candidate("exact_pnp_transistor", _candidate())
    assert result["status"] == "needs_review"
    assert result["errors"] == []
    assert any("actually occurs" in item for item in result["manual_review"])


@pytest.mark.parametrize(("field", "value"), [
    ("part_number", "PBSS5350"), ("part_number", "PBSS4350T"),
    ("package", "TO-92"), ("manufacturer", "Texas Instruments"),
])
def test_rejects_wrong_identity_or_package(field, value):
    candidate = _candidate()
    candidate["fields"][field]["value"] = value
    assert check_candidate("exact_pnp_transistor", candidate)["status"] == "failed"


@pytest.mark.parametrize(("key", "value"), [
    ("source_url", "file:///tmp/source.pdf"), ("page", True),
    ("page", 0), ("excerpt", ""),
])
def test_rejects_unusable_evidence(key, value):
    candidate = _candidate()
    candidate["fields"]["package"]["evidence"][key] = value
    assert check_candidate("exact_pnp_transistor", candidate)["status"] == "failed"


def test_description_is_not_approved_by_keyword_matching():
    candidate = _candidate()
    candidate["fields"]["description"]["value"] = "NPN transistor, continuous current 3 A"
    report = check_candidate("exact_pnp_transistor", candidate)
    assert report["status"] == "needs_review"
    assert any("numeric claim" in item for item in report["manual_review"])


def test_rejects_quantity_proposal():
    candidate = _candidate()
    candidate["fields"]["quantity"] = deepcopy(candidate["fields"]["package"])
    assert check_candidate("exact_pnp_transistor", candidate)["status"] == "failed"


@pytest.mark.parametrize("case_id", ["ambiguous_argb_led", "legacy_transistor_incomplete_identity"])
def test_clarification_cannot_include_verified_metadata(case_id):
    candidate = {"outcome": "needs_clarification", "fields": {}, "clarification": "Do you have markings or a product link?"}
    assert check_candidate(case_id, candidate)["status"] == "needs_review"
    candidate["fields"] = _candidate()["fields"]
    assert check_candidate(case_id, candidate)["status"] == "failed"


def test_user_assertion_must_be_preserved_separately():
    candidate = {
        "outcome": "needs_clarification", "fields": {},
        "clarification": "What markings appear on the part?",
        "user_assertions": {"part_number": "2N2222", "package": "TO-92"},
    }
    assert check_candidate("user_asserted_transistor_package", candidate)["status"] == "needs_review"
    candidate["user_assertions"]["part_number"] = "PN2222A"
    assert check_candidate("user_asserted_transistor_package", candidate)["status"] == "failed"


def test_timer_package_is_ordering_code_specific():
    candidate = _candidate()
    for name, value in {"manufacturer": "Texas Instruments", "part_number": "NE555P", "package": "PDIP-8", "description": "Precision timer"}.items():
        candidate["fields"][name]["value"] = value
    assert check_candidate("exact_timer_package", candidate)["status"] == "needs_review"
    candidate["fields"]["package"]["value"] = "SOIC-8"
    assert check_candidate("exact_timer_package", candidate)["status"] == "failed"


@pytest.mark.parametrize("candidate", [None, [], {"fields": []}, {"fields": {"package": None}}])
def test_malformed_candidate_returns_failure(candidate):
    assert check_candidate("exact_pnp_transistor", candidate)["status"] == "failed"


@pytest.mark.parametrize('evidence', [None, {'page': True, 'excerpt': 'Transformer'}, {'page': 1, 'excerpt': ''}])
def test_mismatch_requires_positive_cited_evidence(evidence):
    candidate = {'outcome': 'no_match', 'fields': {}, 'mismatch_evidence': evidence}
    assert check_candidate('mismatched_datasheet', candidate)['status'] == 'failed'


def test_mismatch_evidence_still_requires_semantic_review():
    candidate = {'outcome': 'no_match', 'fields': {}, 'mismatch_evidence': {
        'page': 1, 'excerpt': 'Synthetic unrelated transformer'}}
    report = check_candidate('mismatched_datasheet', candidate)
    assert report['status'] == 'needs_review' and report['errors'] == []
