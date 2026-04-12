"""
Fine-tuning dataset recorder and exporter.

Records LLM calls (image extraction, description merge) into fine_tune_samples.
Feedback from subsequent user edits is linked by part_id to build preference pairs.

Export format: OpenAI fine-tuning JSONL
  {"messages": [{role, content}, ...]}
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db.persistence import _connect  # reuse connection helper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FineTuneRecorder:
    """
    Records LLM calls to fine_tune_samples and exports JSONL for fine-tuning.

    Attach to LLMClient via client.recorder = FineTuneRecorder(db_path).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = db_path

    def record(
        self,
        call_type: str,
        messages: list[dict],
        response: str,
        part_id: int | None = None,
    ) -> int:
        """
        Save a fine-tuning sample. Returns the new sample id.

        call_type: 'image_extract' | 'description_merge'
        messages:  full messages array sent to the LLM (including base64 image if present)
        response:  raw string returned by the LLM
        part_id:   inventory part this sample relates to (used to link feedback)
        """
        conn = _connect(self._db_path)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO fine_tune_samples
                        (call_type, messages_json, response, part_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (call_type, json.dumps(messages), response, part_id, _now()),
                )
                return cursor.lastrowid
        finally:
            conn.close()

    def set_feedback_for_part(
        self,
        part_id: int,
        call_type: str,
        feedback_response: str,
    ) -> bool:
        """
        Find the most recent sample of call_type for part_id and set its feedback.

        Returns True if a sample was found and updated.
        """
        conn = _connect(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT id FROM fine_tune_samples
                WHERE part_id = ? AND call_type = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (part_id, call_type),
            ).fetchone()
            if row is None:
                return False
            now = _now()
            with conn:
                conn.execute(
                    """
                    UPDATE fine_tune_samples
                    SET feedback_response = ?, feedback_at = ?
                    WHERE id = ?
                    """,
                    (feedback_response, now, row["id"]),
                )
            return True
        finally:
            conn.close()

    def export_jsonl(
        self,
        call_type: str | None = None,
        has_feedback: bool | None = None,
    ) -> str:
        """
        Export samples as OpenAI fine-tuning JSONL.

        Each line: {"messages": [...], "response": "..."}
        When feedback_response is present, response is replaced with feedback_response
        so the sample represents the preferred output.

        call_type:   filter to 'image_extract' or 'description_merge'; None = all
        has_feedback: True = only samples with feedback; False = only without; None = all
        """
        conn = _connect(self._db_path)
        try:
            clauses: list[str] = []
            params: list = []
            if call_type is not None:
                clauses.append("call_type = ?")
                params.append(call_type)
            if has_feedback is True:
                clauses.append("feedback_response IS NOT NULL")
            elif has_feedback is False:
                clauses.append("feedback_response IS NULL")
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT messages_json, response, feedback_response FROM fine_tune_samples {where} ORDER BY created_at",
                params,
            ).fetchall()
        finally:
            conn.close()

        lines: list[str] = []
        for row in rows:
            messages = json.loads(row["messages_json"])
            # Use feedback as the ground-truth response when available.
            assistant_content = row["feedback_response"] if row["feedback_response"] else row["response"]
            # Build OpenAI fine-tuning format: messages array ending with assistant turn.
            ft_messages = [
                m for m in messages if m.get("role") in ("system", "user")
            ]
            ft_messages.append({"role": "assistant", "content": assistant_content})
            lines.append(json.dumps({"messages": ft_messages}))

        return "\n".join(lines)
