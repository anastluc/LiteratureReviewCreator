"""`lit` command-line interface."""
from __future__ import annotations

import json
import sys

import typer

from .project import Project, create_project

for _stream in (sys.stdout, sys.stderr):  # Windows consoles/pipes default to cp1252
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="Literature knowledge-base pipeline", no_args_is_help=True)
rq_app = typer.Typer(help="Stage 1: research questions", no_args_is_help=True)
merge_app = typer.Typer(help="Stage 3: merge Undermind results into the paper list", no_args_is_help=True)
app.add_typer(rq_app, name="rq")
app.add_typer(merge_app, name="merge")

ProjectOpt = typer.Option(..., "--project", "-p", help="Project slug")


@app.command()
def new(slug: str) -> None:
    """Create a project workspace with a concept.md template."""
    p = create_project(slug)
    typer.echo(f"Created {p.root}\nEdit {p.concept}, then run: lit rq draft -p {slug}  (or the generate-rqs skill)")


@rq_app.command("draft")
def rq_draft(project: str = ProjectOpt, overwrite: bool = False) -> None:
    """Generate rqs.draft.yaml from concept.md with the `fast` LLM (checkpoint 1 follows)."""
    from .stages import rq
    p = Project.open(project)
    rqs = rq.run(p, action="draft", overwrite=overwrite)
    for r in rqs:
        typer.echo(f"{r.id}: {r.question}")
    typer.echo(f"\n👤 Review/edit {p.rqs_draft}, then: lit rq approve -p {project}")


@rq_app.command("approve")
def rq_approve(project: str = ProjectOpt) -> None:
    """Freeze the edited draft into rqs.yaml (IDs RQ1..RQn)."""
    from .stages import rq
    rqs = rq.run(Project.open(project), action="approve")
    typer.echo(f"Approved {len(rqs)} RQs. Next: Claude Code skill `undermind-search`.")


@merge_app.command("run")
def merge_run(project: str = ProjectOpt) -> None:
    """Dedupe + enrich Undermind CSVs into papers_review.csv (checkpoint 2 follows)."""
    from .stages import merge
    p = Project.open(project)
    df = merge.run(p, action="merge")
    typer.echo(f"{len(df)} unique papers -> {p.papers_review}\n"
               f"👤 Set include=0 for papers to drop, then: lit merge approve -p {project}")


@merge_app.command("approve")
def merge_approve(project: str = ProjectOpt) -> None:
    """Load the pruned papers_review.csv into papers.sqlite."""
    from .stages import merge
    r = merge.run(Project.open(project), action="approve")
    typer.echo(f"{r['included']}/{r['papers']} papers included, {r['citations']} intra-corpus citation edges.")


@app.command()
def login(project: str | None = typer.Option(None, "--project", "-p"),
          url: str | None = typer.Option(None, help="Start page (default: an EZproxy-prefixed DOI)")) -> None:
    """Open the persistent browser profile so you can sign in via university SSO (password never stored)."""
    from .config import load_config
    from .project import _resolve
    from .stages.retrieve.browser import login as do_login
    profile = Project.open(project).browser_profile_dir if project else \
        _resolve(load_config()["paths"]["browser_profile_dir"])
    do_login(profile, url)


@app.command()
def retrieve(project: str = ProjectOpt, no_browser: bool = typer.Option(False, help="Open access only"),
             retry: bool = typer.Option(False, help="Retry papers previously not found")) -> None:
    """Download PDFs (inbox → open access → browser/EZproxy fallback)."""
    from .stages import retrieve as r
    r.run(Project.open(project), browser=not no_browser, retry=retry)


@app.command()
def parse(project: str = ProjectOpt, retry: bool = False) -> None:
    """Parse PDFs with GROBID into TEI + structured JSON."""
    from .stages import parse as s
    s.run(Project.open(project), retry=retry)


@app.command()
def index(project: str = ProjectOpt, rebuild: bool = False) -> None:
    """Embed abstracts (SPECTER2) and chunks (bge) into LanceDB."""
    from .stages import index as s
    s.run(Project.open(project), rebuild=rebuild)


@app.command("map")
def map_(project: str = ProjectOpt, no_llm: bool = typer.Option(False, help="Keyword labels only")) -> None:
    """Compute t-SNE/UMAP projections, HDBSCAN clusters and labels."""
    from .stages import mapping as s
    r = s.run(Project.open(project), llm_labels=not no_llm)
    typer.echo(f"{r['clusters']} clusters over {r['papers']} papers ({r['noise']} unclustered)")


@app.command()
def report(project: str = ProjectOpt,
           only: str | None = typer.Option(None, help="'rq' or 'thematic' (default: both)")) -> None:
    """Write the RQ-structured report and the thematic appendix."""
    from .stages import report as s
    s.run(Project.open(project), only=only)


@app.command()
def query(question: str, project: str = ProjectOpt,
          rq: list[str] = typer.Option(None, "--rq", help="Restrict to RQ id (repeatable)"),
          paper: list[str] = typer.Option(None, "--paper", help="Restrict to paper id (repeatable)"),
          k: int = typer.Option(0, help="Chunks to retrieve (default: config query.top_k)"),
          no_llm: bool = typer.Option(False, help="Only print retrieved evidence"),
          as_json: bool = typer.Option(False, "--json", help="Machine-readable output")) -> None:
    """Ask the knowledge base (hybrid search + cited answer)."""
    from .query import answer, sources_payload
    from .query import retrieve as do_retrieve
    p = Project.open(project)
    kw = dict(rq_ids=rq or None, paper_ids=paper or None, k=k or None)
    if no_llm:
        chunks, meta = do_retrieve(p, question, **kw)
        out = {"sources": sources_payload(chunks, meta)}
    else:
        out = answer(p, question, **kw)
    if as_json:
        typer.echo(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if "answer" in out:
        typer.echo(out["answer"] + "\n")
        if out["invalid_citations"]:
            typer.echo(f"⚠ invalid citations: {', '.join(out['invalid_citations'])}\n")
    for s in out["sources"]:
        typer.echo(f"[{s['n']}] {s['paper_id']} {s['citation'] or ''} — {s['section']}"
                   + (f" p.{s['page']}" if s["page"] else ""))
        if no_llm:
            typer.echo("    " + s["text"][:400].replace("\n", " ") + "\n")


@app.command()
def status(project: str = ProjectOpt) -> None:
    """Show pipeline progress for a project."""
    p = Project.open(project)
    marks = {"concept.md": p.concept, "rqs.draft.yaml": p.rqs_draft, "rqs.yaml": p.rqs,
             "papers_review.csv": p.papers_review, "map": p.map_dir / "projection.parquet"}
    for name, path in marks.items():
        typer.echo(f"{'✓' if path.exists() else '·'} {name}")
    rq_dirs = sorted(d.name for d in p.undermind_dir.glob("*/") if (d / "papers.csv").exists()) \
        if p.undermind_dir.exists() else []
    typer.echo(f"{'✓' if rq_dirs else '·'} undermind: {', '.join(rq_dirs) or 'none'}")
    if p.db_path.exists():
        with p.connect() as conn:
            for col in ("pdf_status", "parse_status", "index_status"):
                rows = conn.execute(f"SELECT {col}, COUNT(*) FROM papers WHERE included=1 GROUP BY {col}")
                typer.echo(f"  {col}: " + ", ".join(f"{a}={b}" for a, b in rows))
    reports = sorted(f.name for f in p.reports_dir.glob("*.md")) if p.reports_dir.exists() else []
    typer.echo(f"{'✓' if reports else '·'} reports: {', '.join(reports) or 'none'}")


@app.command("run")
def run_all(project: str = ProjectOpt, no_browser: bool = False) -> None:
    """Run every stage that is due, stopping at the two 👤 checkpoints."""
    from .stages import index as s_index
    from .stages import mapping as s_map
    from .stages import merge, rq
    from .stages import parse as s_parse
    from .stages import report as s_report
    from .stages import retrieve as s_retrieve
    p = Project.open(project)
    if not p.rqs_draft.exists() and not p.rqs.exists():
        rq.run(p, action="draft")
        typer.echo(f"👤 Checkpoint 1: review {p.rqs_draft}, then `lit rq approve -p {project}`")
        raise typer.Exit()
    if not p.rqs.exists():
        typer.echo(f"👤 Checkpoint 1 pending: review {p.rqs_draft}, then `lit rq approve -p {project}`")
        raise typer.Exit()
    if not any(p.undermind_dir.glob("*/*.csv")):
        typer.echo("Next: run the Claude Code skill `undermind-search` for this project.")
        raise typer.Exit()
    if not p.papers_review.exists():
        merge.run(p, action="merge")
        typer.echo(f"👤 Checkpoint 2: prune {p.papers_review}, then `lit merge approve -p {project}`")
        raise typer.Exit()
    with p.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    if not n:
        typer.echo(f"👤 Checkpoint 2 pending: prune {p.papers_review}, then `lit merge approve -p {project}`")
        raise typer.Exit()
    s_retrieve.run(p, browser=not no_browser)
    try:
        s_parse.run(p)
    except RuntimeError as e:
        typer.echo(f"⚠ parse skipped: {e}")
    s_index.run(p)
    s_map.run(p)
    s_report.run(p)
    typer.echo(f"Done. Explore with: lit serve  (project {project})")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the FastAPI backend (serves the built frontend too)."""
    import uvicorn
    uvicorn.run("litkb.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
