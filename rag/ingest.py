"""Load runbooks/*.md into Postgres: one row per section, with an embedding and a full-text index.

    python -m rag.ingest

Chunking follows the runbook's own structure (Symptoms / Confirm / Likely causes / Fix / Verify /
Prevent) instead of fixed-size windows, so each chunk is one coherent piece of advice and a
search hit can be cited as runbooks/<file>.md#<section>.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import db
from .embed import DIM, MODEL, embed_passages

RUNBOOKS_DIR = Path(__file__).resolve().parent.parent / "runbooks"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS runbook_chunks (
    id          text PRIMARY KEY,               -- '<runbook>#<section-slug>'
    runbook_id  text NOT NULL,
    title       text NOT NULL,
    section     text NOT NULL,
    category    text NOT NULL,
    components  text[] NOT NULL,                -- e.g. {{redis-cart,cartservice}} or {{any}}
    keywords    text[] NOT NULL,
    keyword_text text NOT NULL,                 -- keywords joined, for the full-text index
    content     text NOT NULL,
    embedding   vector({DIM}) NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', title || ' ' || keyword_text), 'A') ||
                    setweight(to_tsvector('english', section), 'B') ||
                    setweight(to_tsvector('english', content), 'C')) STORED
);
CREATE INDEX IF NOT EXISTS runbook_chunks_embedding ON runbook_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS runbook_chunks_tsv ON runbook_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS runbook_chunks_category ON runbook_chunks (category);
"""


@dataclass
class Chunk:
    id: str
    runbook_id: str
    title: str
    section: str
    category: str
    components: list[str]
    keywords: list[str]
    content: str

    def embed_text(self) -> str:
        # Give every chunk its context: which runbook, which section, which keywords.
        return f"{self.title}. {self.section}.\nKeywords: {', '.join(self.keywords)}\n{self.content}"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse_runbook(path: Path) -> list[Chunk]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError(f"{path.name}: missing front matter")
    meta, body = yaml.safe_load(m.group(1)), m.group(2)
    for key in ("id", "title", "category", "components", "keywords"):
        if key not in meta:
            raise ValueError(f"{path.name}: front matter needs '{key}'")

    # Split on "## " headings; text before the first heading (intro) becomes an "Overview" chunk.
    parts = re.split(r"^## +(.+)$", body, flags=re.M)
    sections = [("Overview", re.sub(r"^# .*$", "", parts[0], flags=re.M).strip())]
    sections += [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts), 2)]
    return [Chunk(id=f"{meta['id']}#{slug(name)}", runbook_id=meta["id"], title=meta["title"],
                  section=name, category=meta["category"], components=list(meta["components"]),
                  keywords=list(meta["keywords"]), content=content)
            for name, content in sections if content]


def load_all() -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(RUNBOOKS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        chunks.extend(parse_runbook(path))
    return chunks


def main() -> None:
    start = time.monotonic()
    chunks = load_all()
    print(f"Parsed {len(chunks)} sections from {len({c.runbook_id for c in chunks})} runbooks")
    print(f"Embedding with {MODEL} (local)...")
    vectors = embed_passages([c.embed_text() for c in chunks])
    with db.connect() as conn, conn.transaction():
        conn.execute(SCHEMA)
        conn.execute("DELETE FROM runbook_chunks")  # full reload: runbooks are small and versioned in git
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO runbook_chunks (id, runbook_id, title, section, category, components, keywords, keyword_text, content, embedding)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [(c.id, c.runbook_id, c.title, c.section, c.category, c.components, c.keywords, " ".join(c.keywords), c.content, v)
                 for c, v in zip(chunks, vectors)])
        count = conn.execute("SELECT count(*) FROM runbook_chunks").fetchone()[0]
    print(f"Stored {count} sections in Postgres in {time.monotonic() - start:.1f}s")


if __name__ == "__main__":
    main()
