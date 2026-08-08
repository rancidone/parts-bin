"""Deterministic checks for whether extracted inventory data is actionable."""


def missing_fields(record: dict) -> list[str]:
    missing: list[str] = []
    if not record.get("part_category"):
        missing.append("part_category")
    profile = record.get("profile")
    if not profile:
        missing.append("profile")
    if record.get("quantity") is None:
        missing.append("quantity")
    if profile == "passive":
        if not record.get("value"):
            missing.append("value")
        if not record.get("package"):
            missing.append("package")
    elif profile == "discrete_ic" and not record.get("part_number"):
        missing.append("part_number")
    return missing


def is_complete(record: dict) -> bool:
    return not missing_fields(record)


def clarification_prompt(record: dict) -> str:
    fields = missing_fields(record)
    labels = ", ".join(fields)
    return f"I need the following information before adding this part: {labels}."
