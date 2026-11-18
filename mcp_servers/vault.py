"""Knowledge-vault loader shared by the RAG and graph servers.

Parses Obsidian-style notes (YAML frontmatter + body + [[wikilinks]]), splits
bodies into paragraph chunks, and builds the wikilink graph.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WIKILINK = re.compile(r"\[\[([a-z0-9][a-z0-9-]{2,63})\]\]")
DEFAULT_VAULT = Path(__file__).resolve().parent.parent / "knowledge_vault"


@dataclass(frozen=True)
class Note:
    note_id: str
    domain: str
    risk_tier: str
    version: str
    body: str
    links: tuple[str, ...]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    chunk_hash: str
    note_id: str
    text: str


@dataclass
class Vault:
    notes: dict[str, Note] = field(default_factory=dict)
    chunks: list[Chunk] = field(default_factory=list)
    out_edges: dict[str, set[str]] = field(default_factory=dict)

    @property
    def version(self) -> str:
        """Content hash of the vault; used as index_version."""
        h = hashlib.sha256()
        for nid in sorted(self.notes):
            h.update(nid.encode())
            h.update(self.notes[nid].body.encode())
        return h.hexdigest()[:12]


def parse_note(text: str) -> Note:
    if not text.startswith("---\n"):
        raise ValueError("note is missing YAML frontmatter")
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front)
    links = tuple(dict.fromkeys(WIKILINK.findall(body)))
    return Note(
        note_id=str(meta["id"]),
        domain=str(meta["domain"]),
        risk_tier=str(meta["risk_tier"]),
        version=str(meta["version"]),
        body=body.strip(),
        links=links,
    )


def load_vault(root: Path = DEFAULT_VAULT) -> Vault:
    vault = Vault()
    for path in sorted(root.glob("*.md")):
        note = parse_note(path.read_text(encoding="utf-8"))
        vault.notes[note.note_id] = note
        vault.out_edges[note.note_id] = {t for t in note.links if t != note.note_id}
    for note in vault.notes.values():
        paragraphs = [p.strip() for p in note.body.split("\n\n") if p.strip()]
        for i, para in enumerate(paragraphs):
            cid = "chk_" + hashlib.sha256(f"{note.note_id}:{i}".encode()).hexdigest()[:16]
            chash = hashlib.sha256(para.encode()).hexdigest()
            vault.chunks.append(Chunk(cid, chash, note.note_id, para))
    return vault


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~1.3 tokens per whitespace word."""
    return math.ceil(len(text.split()) * 1.3)
