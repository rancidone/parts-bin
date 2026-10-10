"""Offline checks for enrichment candidates; source/semantic review remains manual."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


FIXTURES = Path(__file__).parent / "enrichment"
FIELDS = {"manufacturer", "part_number", "package", "description"}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_candidate(case_id: str, candidate: object) -> dict:
    """Return failed or needs_review, never claim source verification from JSON alone."""
    cases = json.loads((FIXTURES / "acceptance.json").read_text())["cases"]
    case = next((item for item in cases if item["id"] == case_id), None)
    if case is None:
        raise ValueError(f"Unknown case: {case_id}")
    errors: list[str] = []
    reviews = list(case["must_not"])

    def result() -> dict:
        return {
            "case_id": case_id,
            "status": "failed" if errors else "needs_review",
            "errors": errors,
            "manual_review": reviews,
        }

    if not isinstance(candidate, dict):
        errors.append("Candidate must be a JSON object.")
        return result()
    allowed = {"outcome", "fields", "user_assertions", "clarification", "mismatch_evidence"}
    if set(candidate) - allowed:
        errors.append("Unexpected top-level fields: " + ", ".join(sorted(set(candidate) - allowed)))
    expected = case.get("expected_fields", {})
    if "expected_output_file" in case:
        fixture = json.loads((FIXTURES / case["expected_output_file"]).read_text())
        expected = {key: item["value"] for key, item in fixture["expected_fields"].items()}
        aliases = {key: item.get("accepted_aliases", []) for key, item in fixture["expected_fields"].items()}
        reviews.extend(item["rule"] for item in fixture.get("additional_correctness_checks", []))
    else:
        aliases = case.get("accepted_aliases", {})

    expected_outcome = case.get("expected_outcome", "proposal" if expected else "needs_clarification")
    if candidate.get("outcome") != expected_outcome:
        errors.append(f"Expected outcome {expected_outcome!r}.")
    if candidate.get("user_assertions", {}) != case.get("user_assertions", {}):
        errors.append("Preserve the case's user assertions separately and without changes.")
    fields = candidate.get("fields")
    if not isinstance(fields, dict):
        errors.append("fields must be an object.")
        return result()
    if set(fields) - FIELDS:
        errors.append("Unsupported proposed fields: " + ", ".join(sorted(set(fields) - FIELDS)))

    if expected_outcome == 'no_match':
        if fields:
            errors.append('A mismatched source must not propose fields.')
        evidence = candidate.get('mismatch_evidence')
        if (not isinstance(evidence, dict) or set(evidence) != {'page', 'excerpt'}
                or type(evidence['page']) is not int or evidence['page'] < 1
                or not _text(evidence['excerpt'])):
            errors.append('A mismatched source requires a cited passage identifying the conflict.')
        reviews.append('Verify the cited passage identifies an unrelated device, rather than merely omitting the requested code.')
        return result()

    if not expected:
        if fields:
            errors.append("This clarification case must not propose verified metadata yet.")
        if not _text(candidate.get("clarification")):
            errors.append("A nonempty clarification question is required.")
        reviews.append("Check that the question targets missing identity information and does not embed unsupported claims.")
        return result()

    if set(expected) - set(fields):
        errors.append("Missing required fields: " + ", ".join(sorted(set(expected) - set(fields))))
    for name, field in fields.items():
        if not isinstance(field, dict) or set(field) != {"value", "evidence"}:
            errors.append(f"{name}: expected value and evidence only.")
            continue
        value = field["value"]
        if not _text(value):
            errors.append(f"{name}: value must be a nonempty string.")
        elif name in expected and name != "description":
            choices = [expected[name], *aliases.get(name, [])]
            if value.strip().casefold() not in {choice.casefold() for choice in choices}:
                errors.append(f"{name}: value does not match the expected identity/package.")
        evidence = field["evidence"]
        if not isinstance(evidence, dict) or set(evidence) != {"source_url", "page", "excerpt"}:
            errors.append(f"{name}: evidence requires source_url, page, and excerpt only.")
            continue
        url = evidence["source_url"]
        try:
            parsed = urlparse(url) if isinstance(url, str) else None
            valid_url = parsed is not None and parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        if not valid_url:
            errors.append(f"{name}: evidence must reference an HTTPS source URL without credentials.")
        if type(evidence["page"]) is not int or evidence["page"] < 1:
            errors.append(f"{name}: page must be a positive, one-based integer.")
        if not _text(evidence["excerpt"]):
            errors.append(f"{name}: a source excerpt is required.")

    reviews.extend([
        "Open each cited source: confirm authority, exact part identity, page, and that the excerpt actually occurs there.",
        "Verify that each excerpt supports its proposed value, including package aliases.",
        "Review description meaning and every numeric claim; field presence is not semantic correctness.",
        "This offline result cannot establish that an application preserved inventory; integration checks must inspect database state.",
    ])
    return result()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    try:
        candidate = json.loads(args.candidate.read_text())
        report = check_candidate(args.case_id, candidate)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    # A review-required result is deliberately not a successful acceptance run.
    return 1 if report["status"] == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
