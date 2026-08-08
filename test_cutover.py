"""Structural guardrails for the one-way conversational cutover."""

from pathlib import Path


ROOT = Path(__file__).parent
EXCLUDED_DIRECTORIES = {".git", ".venv", "node_modules", "dist", "__pycache__"}
PROHIBITED = (
    "db" + "_action",
    "Conversation" + "History",
    "LLM" + "Client",
    "/" + "chat",
    "/" + "query",
    "settings/" + "llm",
    "primary" + "_backend",
    "secondary" + "_backend",
    "use" + "_secondary",
    "[" + "llm]",
    "[" + "llama]",
)


def test_repository_contains_only_the_agent_conversation_contract():
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_DIRECTORIES for part in path.parts):
            continue
        if path.suffix not in {".md", ".py", ".ts", ".tsx", ".toml", ".json", ".css"}:
            continue
        content = path.read_text(errors="ignore")
        if any(fragment in content for fragment in PROHIBITED):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"removed conversational artifacts remain: {', '.join(offenders)}"
