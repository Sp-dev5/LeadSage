"""
The seller's own documents (case studies, product notes, answers to objections) as a small
knowledge base the email drafts and the ideal-customer page can quote from.

Files in data/kb/ (.txt, .md, .pdf) are split into passages of about PASSAGE_CHARS characters
along paragraph breaks, each passage is turned into a vector by the local embedding model (see
agent/llm.py), and both are kept in the lead store's kb_passages table. A file is read again only
when its bytes change. Finding passages for a lead compares a query built from the lead's checked
facts with every passage and keeps the closest ones above a similarity floor.

Scoring never reads this: the knowledge base changes what the emails may say, never a fit or a
verdict. A passage that talks to an AI ("ignore your instructions") is kept out of every prompt.

Run it:  python -m agent.kb                      index data/kb/
         python -m agent.kb --folder data/kb_sample
         python -m agent.kb --check              how many test queries find their passage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
from array import array
from contextlib import closing
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel

from agent import store, trace
from agent.fetch import now
from agent.llm import embed, embed_model_name

DATA = Path(__file__).resolve().parent.parent / "data"
KB_DIR = DATA / "kb"
SAMPLE_DIR = DATA / "kb_sample"
CHECK_FILE = Path(__file__).resolve().parent.parent / "tests" / "kb_retrieval_cases.json"
SUFFIXES = (".txt", ".md", ".pdf")
MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 200
PASSAGE_CHARS = 1000
QUERY_CHARS = 400
INSTRUCTIONS = re.compile(
    r"ignore (?:all |any |your |the )?(?:earlier |previous |prior |above |other )?instructions"
    r"|(?:note|message|instructions?) (?:to|for) (?:the |any )?(?:ai|model|assistant|llm|language model)"
    r"|\b(?:ai|assistant|model|llm)s? (?:reading|writing|drafting) (?:this|an email|about us)"
    r"|^\s*(?:system|assistant)\s*:"
    r"|you are (?:an? |the )?(?:ai|assistant|language model)",
    re.I | re.M)

SCHEMA = """CREATE TABLE IF NOT EXISTS kb_files (
    file TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    pages INTEGER NOT NULL,
    empty_pages INTEGER NOT NULL,
    passages INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    error TEXT NOT NULL,
    model TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_passages (
    file TEXT NOT NULL,
    page INTEGER NOT NULL,
    n INTEGER NOT NULL,
    text TEXT NOT NULL,
    vector BLOB NOT NULL,
    skipped INTEGER NOT NULL,
    PRIMARY KEY (file, page, n)
);"""


class Passage(BaseModel):
    """One piece of a document. `page` is 0 for a text or Markdown file, which has no pages."""

    file: str
    page: int
    n: int
    text: str

    @property
    def key(self) -> str:
        return f"{self.file}#{self.page}#{self.n}"

    @property
    def where(self) -> str:
        return f"{self.file}, page {self.page}" if self.page else self.file

    @property
    def source(self) -> str:
        return f"your document: {self.where}"


class Found(NamedTuple):
    passage: Passage
    similarity: float


class FileInfo(BaseModel):
    file: str
    pages: int
    empty_pages: int
    passages: int
    skipped: int
    error: str
    indexed_at: str


def _connect(path: Path) -> sqlite3.Connection:
    conn = store.open_db(path)
    conn.executescript(SCHEMA)
    return conn


def _writing(path: Path):
    return store.writing(path, _connect)


def clean_name(name: str) -> str:
    """A safe file name for an upload: no folders, only letters, digits, dot, dash, underscore and
    space, at most 80 characters, ending in an allowed type. "" if nothing usable is left."""
    base = re.split(r"[\\/]", name.strip())[-1]
    stem, dot, suffix = base.rpartition(".")
    suffix = f".{suffix.lower()}" if dot else ""
    stem = re.sub(r"[^\w .-]+", "", stem, flags=re.ASCII).strip(" .-")
    stem = re.sub(r"\s+", " ", stem)[:80 - len(suffix)]
    return f"{stem}{suffix}" if stem and suffix in SUFFIXES else ""


def read_pages(path: Path) -> list[tuple[int, str]]:
    """(page, text) for each page of a PDF; one (0, text) for a text or Markdown file. Raises
    ValueError for a PDF over MAX_PAGES."""
    if path.suffix.lower() != ".pdf":
        return [(0, path.read_text(encoding="utf-8", errors="replace"))]
    from pypdf import PdfReader
    reader = PdfReader(path)
    if len(reader.pages) > MAX_PAGES:
        raise ValueError(f"{len(reader.pages)} pages, over the {MAX_PAGES}-page limit")
    return [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s]


def split(text: str, size: int = PASSAGE_CHARS) -> list[str]:
    """Passages of about `size` characters: whole paragraphs joined until the next would pass
    `size` or a Markdown heading starts a new section; a paragraph longer than that is cut
    between sentences."""
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    pieces: list[str] = []
    for p in (p for p in paragraphs if p):
        if len(p) <= size:
            pieces.append(p)
            continue
        part = ""
        for s in _sentences(p):
            if part and len(part) + len(s) + 1 > size:
                pieces.append(part)
                part = ""
            part = f"{part} {s}".strip()
        if part:
            pieces.append(part)
    passages: list[str] = []
    for piece in pieces:
        if passages and not piece.startswith("#") and len(passages[-1]) + len(piece) + 2 <= size:
            passages[-1] = f"{passages[-1]}\n\n{piece}"
        else:
            passages.append(piece)
    return passages


def talks_to_a_model(text: str) -> bool:
    """True if the passage reads like instructions to an AI rather than something about us."""
    return bool(INSTRUCTIONS.search(text))


def _pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def _unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def files_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.is_file() and p.suffix.lower() in SUFFIXES) if folder.exists() else []


class Prepared(NamedTuple):
    """A file read and embedded, ready to be stored. Nothing here touched the database."""

    pages: list[tuple[int, str]]
    rows: list[tuple[int, int, str]]
    vectors: list[list[float]]
    error: str


def _prepare(path: Path) -> Prepared:
    """Read, split and embed one file. This is the slow part (a long PDF can take minutes), so it
    runs with no transaction open: the store stays writable for everything else meanwhile."""
    pages, error, rows = [], "", []
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError(f"over the {MAX_BYTES // (1024 * 1024)} MB limit")
        pages = read_pages(path)
    except Exception as e:
        error = str(e)[:200]
    for page, text in pages:
        rows += [(page, n, t) for n, t in enumerate(split(text))]
    vectors = embed([t for _, _, t in rows]) if rows else []
    return Prepared(pages, rows, vectors, error)


def _store_file(conn: sqlite3.Connection, path: Path, digest: str, ready: Prepared) -> FileInfo:
    conn.execute("DELETE FROM kb_passages WHERE file = ?", (path.name,))
    skipped = 0
    for (page, n, text), vector in zip(ready.rows, ready.vectors):
        bad = talks_to_a_model(text)
        skipped += bad
        conn.execute("INSERT INTO kb_passages (file, page, n, text, vector, skipped) VALUES (?, ?, ?, ?, ?, ?)",
                     (path.name, page, n, text, _pack(vector), int(bad)))
    info = FileInfo(file=path.name, pages=len(ready.pages), empty_pages=sum(not t.strip() for _, t in ready.pages),
                    passages=len(ready.rows), skipped=skipped, error=ready.error, indexed_at=now())
    conn.execute("INSERT OR REPLACE INTO kb_files (file, hash, pages, empty_pages, passages, skipped, error, model, "
                 "indexed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (info.file, digest, info.pages, info.empty_pages, info.passages, info.skipped, info.error,
                  embed_model_name(), info.indexed_at))
    return info


def index(folder: Path = KB_DIR, path: Path = store.DB_PATH, force: bool = False,
          report=print) -> list[FileInfo]:
    """Bring the index in line with `folder`: new or changed files are read and embedded, files
    no longer there are dropped, unchanged ones are left alone (all of them again with `force`)."""
    present = {p.name: p for p in files_in(folder)}
    done = []
    with closing(_connect(path)) as conn:
        known = {r["file"]: (r["hash"], r["model"]) for r in conn.execute("SELECT file, hash, model FROM kb_files")}
    for gone in set(known) - set(present):
        with _writing(path) as conn:
            conn.execute("DELETE FROM kb_passages WHERE file = ?", (gone,))
            conn.execute("DELETE FROM kb_files WHERE file = ?", (gone,))
        report(f"{gone}: removed from the index")
    for name, file in present.items():
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if not force and known.get(name) == (digest, embed_model_name()):
            continue
        ready = _prepare(file)
        with _writing(path) as conn:
            info = _store_file(conn, file, digest, ready)
        done.append(info)
        report(f"{name}: {info.passages} passages" + (f", {info.skipped} kept out (they talk to an AI)"
                                                      if info.skipped else "")
               + (f", {info.empty_pages} pages with no text" if info.empty_pages else "")
               + (f" ({info.error})" if info.error else ""))
    return done


def listing(path: Path = store.DB_PATH) -> list[FileInfo]:
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT * FROM kb_files ORDER BY file").fetchall()
    return [FileInfo(file=r["file"], pages=r["pages"], empty_pages=r["empty_pages"], passages=r["passages"],
                     skipped=r["skipped"], error=r["error"], indexed_at=r["indexed_at"]) for r in rows]


def forget_file(name: str, folder: Path = KB_DIR, path: Path = store.DB_PATH) -> bool:
    """Delete a document and its passages. False if there was no such file."""
    target = folder / name
    found = target.is_file() and target.parent.resolve() == folder.resolve()
    if found:
        target.unlink()
    with _writing(path) as conn:
        conn.execute("DELETE FROM kb_passages WHERE file = ?", (name,))
        conn.execute("DELETE FROM kb_files WHERE file = ?", (name,))
    return found


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _passages(path: Path) -> list[tuple[Passage, list[float]]]:
    with closing(_connect(path)) as conn:
        rows = conn.execute("SELECT * FROM kb_passages WHERE skipped = 0 ORDER BY file, page, n").fetchall()
    return [(Passage(file=r["file"], page=r["page"], n=r["n"], text=r["text"]), _unpack(r["vector"])) for r in rows]


def search(query: str, top_k: int, min_similarity: float, path: Path = store.DB_PATH,
           exclude: set[str] = frozenset()) -> list[Found]:
    """The `top_k` passages closest to `query` with a similarity of at least `min_similarity`,
    best first, leaving out the passage keys in `exclude`."""
    stored = [(p, v) for p, v in _passages(path) if p.key not in exclude]
    if not stored or top_k <= 0:
        return []
    [q] = embed([query], "query")
    ranked = sorted((Found(p, round(_cosine(q, v), 4)) for p, v in stored), key=lambda f: -f.similarity)
    return [f for f in ranked[:top_k] if f.similarity >= min_similarity]


def lead_query(facts: dict[str, str], industry: str, description: str) -> str:
    """What a lead looks like to the search: the checked facts about what it sells and who to, its
    industry, and the start of its own description."""
    parts = [f"Sells: {facts.get('what_they_sell') or 'unknown'}", f"Customers: {facts.get('customers', 'unknown')}",
             f"Industry: {industry or 'unknown'}", description[:QUERY_CHARS]]
    return ". ".join(p.strip() for p in parts if p.strip())


def proof_points(lead: store.StoredLead, top_k: int, min_similarity: float, path: Path = store.DB_PATH,
                 exclude: set[str] = frozenset()) -> list[Found]:
    """The passages to offer the email writer for this lead. Writes the search into the trace."""
    if top_k <= 0 or not listing(path):
        return []
    query = lead_query(lead.score.facts, lead.company.industry, lead.company.description)
    found = search(query, top_k, min_similarity, path, exclude)
    trace.step("Find proof points", [{"where": f.passage.where, "similarity": f.similarity, "text": f.passage.text}
                                     for f in found], input=query)
    return found


def retrieval_check(top_k: int = 3, path: Path = store.DB_PATH, cases: Path = CHECK_FILE) -> dict:
    """Run the written-down test queries against the index and say how many find their passage in
    the top `top_k`. Each case names the file and a phrase the right passage contains."""
    rows = json.loads(cases.read_text(encoding="utf-8"))
    results = []
    for case in rows:
        found = search(case["query"], top_k, -1.0, path)
        hit = next((i for i, f in enumerate(found, 1)
                    if f.passage.file == case["file"] and case["contains"].lower() in f.passage.text.lower()), 0)
        results.append({"query": case["query"][:60], "rank": hit,
                        "similarity": found[hit - 1].similarity if hit else None,
                        "best_other": max((f.similarity for i, f in enumerate(found, 1) if i != hit), default=None)})
    return {"hits": sum(r["rank"] > 0 for r in results), "cases": len(results), "results": results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=str(KB_DIR), help="folder of documents to index (default: data/kb)")
    ap.add_argument("--force", action="store_true", help="read every file again, changed or not")
    ap.add_argument("--check", action="store_true", help="run the retrieval test queries after indexing")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    folder = Path(args.folder)
    print(f"Indexing {folder} into {store.DB_PATH.name}", flush=True)
    index(folder, force=args.force, report=lambda m: print(m, flush=True))
    for info in listing():
        print(f"  {info.file}: {info.passages} passages, {info.pages or 'no'} pages")
    if args.check:
        result = retrieval_check()
        for r in result["results"]:
            print(f"  rank {r['rank'] or '-'}  {r['similarity']}  {r['query']}")
        print(f"{result['hits']} of {result['cases']} queries found their passage in the top 3")


if __name__ == "__main__":
    main()
