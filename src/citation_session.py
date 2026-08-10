"""External, privacy-first citation state for Word and WPS documents."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path


SESSION_VERSION = 1


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def document_session_key(target: dict) -> str:
    identity = "|".join((
        str(target.get("app_id", "unknown")).casefold(),
        str(target.get("full_name") or target.get("document_name") or
            "unsaved").casefold(),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


class CitationSessionStore:
    """Persists citation numbering outside the manuscript by default."""

    def __init__(self, project_dir: str):
        self.directory = Path(project_dir) / "citation_sessions"

    def _path(self, target: dict) -> Path:
        return self.directory / (document_session_key(target) + ".json")

    def load(self, target: dict) -> dict:
        path = self._path(target)
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    loaded.setdefault("citations", {})
                    loaded.setdefault("history", [])
                    loaded.setdefault("smart_groups", {})
                    return loaded
            except (OSError, ValueError, TypeError):
                pass
        return {
            "version": SESSION_VERSION,
            "document": {
                "app_id": target.get("app_id", ""),
                "document_name": target.get("document_name", ""),
                "full_name": target.get("full_name", ""),
            },
            "citations": {},
            "history": [],
            "smart_groups": {},
            "created_at": _now(),
            "updated_at": _now(),
        }

    def save(self, target: dict, session: dict):
        self.directory.mkdir(parents=True, exist_ok=True)
        session["version"] = SESSION_VERSION
        session["updated_at"] = _now()
        path = self._path(target)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8")
        temporary.replace(path)

    def prepare(self, target: dict, paper_ids: list[str]) -> tuple[dict, dict, dict]:
        """Assign stable numbers and opaque tokens for one document."""
        session = self.load(target)
        citations = session.setdefault("citations", {})
        next_number = max(
            (int(item.get("number", 0) or 0)
             for item in citations.values()), default=0) + 1
        numbers = {}
        tokens = {}
        for paper_id in paper_ids:
            item = citations.get(paper_id)
            if not isinstance(item, dict):
                item = {
                    "number": next_number,
                    "token": "LB-" + uuid.uuid4().hex,
                    "first_inserted_at": _now(),
                }
                citations[paper_id] = item
                next_number += 1
            item.setdefault("token", "LB-" + uuid.uuid4().hex)
            numbers[paper_id] = int(item["number"])
            tokens[paper_id] = str(item["token"])
        self.save(target, session)
        return session, numbers, tokens

    def create_smart_group(self, target: dict, session: dict,
                           paper_ids: list[str]) -> str:
        """Create one short embedded token that resolves only in local state."""
        token = "LB-" + uuid.uuid4().hex
        session.setdefault("smart_groups", {})[token] = {
            "paper_ids": list(paper_ids),
            "created_at": _now(),
        }
        self.save(target, session)
        return token

    def record_insertion(self, target: dict, session: dict,
                         paper_ids: list[str], scheme_id: str,
                         content_mode: str, privacy_mode: str,
                         text: str):
        session.setdefault("history", []).append({
            "paper_ids": list(paper_ids),
            "scheme_id": scheme_id,
            "content_mode": content_mode,
            "privacy_mode": privacy_mode,
            "preview": str(text)[:300],
            "inserted_at": _now(),
        })
        session["history"] = session["history"][-500:]
        self.save(target, session)
