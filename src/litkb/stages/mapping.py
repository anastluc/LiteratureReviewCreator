"""Stage 7: literature map (D15).

SPECTER2 vectors -> PCA(50) -> t-SNE (openTSNE) and UMAP 2-D (both stored);
HDBSCAN on a 10-D UMAP space; clusters labelled by the `fast` LLM (TF-IDF keywords as fallback).
Outputs map/projection.parquet, map/clusters.json, and cluster_id in the LanceDB `papers` table.
"""
from __future__ import annotations

import json
from collections.abc import Callable

import numpy as np
import pandas as pd

from .. import kb
from ..llm import get_provider
from ..project import Project
from ..util import parse_json_block

Log = Callable[[str], None]

LABEL_PROMPT = """These papers form one cluster of a literature map for a review on the topic below.
Give the cluster a short label (2-6 words, specific, no "Cluster" or "Papers on") and a 1-2 sentence description
of what unites them. Return ONLY JSON: {{"label": "...", "description": "..."}}

<review_topic>{topic}</review_topic>
<papers>
{papers}
</papers>"""


def project_2d(X: np.ndarray, cfg: dict, seed: int = 42) -> dict[str, np.ndarray]:
    import umap
    from openTSNE import TSNE
    from sklearn.decomposition import PCA
    n = len(X)
    Xp = PCA(n_components=min(cfg["pca_dims"], n - 1, X.shape[1]), random_state=seed).fit_transform(X)
    perplexity = max(2.0, min(cfg["tsne_perplexity"], (n - 1) / 3))
    tsne = np.asarray(TSNE(perplexity=perplexity, random_state=seed, n_jobs=1, verbose=False).fit(Xp))
    nn = max(2, min(cfg["umap_neighbors"], n - 1))
    u2 = umap.UMAP(n_components=2, n_neighbors=nn, min_dist=0.1, metric="cosine",
                   random_state=seed, compatibility_layout=True).fit_transform(Xp)
    u_hi = umap.UMAP(n_components=max(2, min(cfg["cluster_dims"], n - 2)), n_neighbors=nn, min_dist=0.0,
                     metric="cosine", random_state=seed, compatibility_layout=True).fit_transform(Xp)
    return {"tsne": tsne, "umap": u2, "cluster_space": u_hi}


def cluster(Z: np.ndarray, min_cluster_size: int) -> np.ndarray:
    from sklearn.cluster import HDBSCAN
    mcs = max(2, min(min_cluster_size, len(Z) // 3 or 2))
    return HDBSCAN(min_cluster_size=mcs, copy=True).fit_predict(Z)


def tfidf_keywords(docs: list[str], labels: np.ndarray, top: int = 6) -> dict[int, list[str]]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    ids = sorted(set(labels) - {-1})
    if not ids:
        return {}
    joined = [" ".join(d for d, lab in zip(docs, labels) if lab == c) for c in ids]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_df=0.8 if len(ids) > 2 else 1.0,
                          min_df=1, sublinear_tf=True)
    M = vec.fit_transform(joined)
    terms = np.array(vec.get_feature_names_out())
    return {c: list(terms[np.asarray(M[i].todense()).ravel().argsort()[::-1][:top]]) for i, c in enumerate(ids)}


def run(project: Project, *, llm_labels: bool = True, log: Log = print) -> dict:
    cfg = project.config
    mcfg = cfg["map"]
    papers = kb.all_papers(project)
    if len(papers) < 5:
        raise ValueError(f"Need at least 5 indexed papers for a map (have {len(papers)}); run `lit index`")
    X = np.array([p["vector"] for p in papers], dtype="float32")
    log(f"Projecting {len(X)} papers (t-SNE + UMAP)")
    proj = project_2d(X, mcfg)
    labels = cluster(proj["cluster_space"], mcfg["hdbscan_min_cluster_size"])
    ids = sorted(set(labels) - {-1})
    log(f"HDBSCAN: {len(ids)} clusters, {(labels == -1).sum()} unclustered")

    with project.connect() as conn:
        meta = {r["id"]: dict(r) for r in conn.execute("SELECT id, cited_by_count, venue FROM papers")}
    docs = [f"{p['title']}. {p['abstract'] or ''}" for p in papers]
    keywords = tfidf_keywords(docs, labels)
    topic = project.concept.read_text(encoding="utf-8")[:1500] if project.concept.exists() else ""
    llm = None
    if llm_labels:
        try:
            llm = get_provider("fast", cfg)
        except Exception as e:
            log(f"  no LLM for labels ({e}); using keywords")

    clusters = []
    Z = proj["cluster_space"]
    for c in ids:
        idx = np.where(labels == c)[0]
        centroid = Z[idx].mean(axis=0)
        order = idx[np.argsort(((Z[idx] - centroid) ** 2).sum(axis=1))]
        top = [papers[i] for i in order[:mcfg["label_top_papers"]]]
        label = ", ".join(keywords.get(c, [])[:3]) or f"Cluster {c}"
        desc = ""
        if llm is not None:
            listing = "\n".join(f"- {p['title']} ({p['year']}): {(p['abstract'] or '')[:400]}" for p in top)
            try:
                out = parse_json_block(llm.complete([{"role": "user", "content": LABEL_PROMPT.format(
                    topic=topic, papers=listing)}], max_tokens=400))
                label, desc = out.get("label") or label, out.get("description") or ""
            except Exception as e:
                log(f"  label for cluster {c} failed ({type(e).__name__}); using keywords")
        clusters.append({"id": int(c), "label": label, "description": desc, "size": len(idx),
                         "keywords": keywords.get(c, []), "top_papers": [p["paper_id"] for p in top],
                         "centroid_umap": proj["umap"][idx].mean(axis=0).round(4).tolist(),
                         "centroid_tsne": proj["tsne"][idx].mean(axis=0).round(4).tolist()})
        log(f"  [{c}] {label} ({len(idx)})")

    df = pd.DataFrame({
        "paper_id": [p["paper_id"] for p in papers], "title": [p["title"] for p in papers],
        "year": [p["year"] for p in papers], "rq_ids": [";".join(p["rq_ids"] or []) for p in papers],
        "cited_by_count": [(meta.get(p["paper_id"]) or {}).get("cited_by_count") for p in papers],
        "venue": [(meta.get(p["paper_id"]) or {}).get("venue") for p in papers],
        "tsne_x": proj["tsne"][:, 0], "tsne_y": proj["tsne"][:, 1],
        "umap_x": proj["umap"][:, 0], "umap_y": proj["umap"][:, 1], "cluster_id": labels.astype(int),
    })
    project.map_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(project.map_dir / "projection.parquet", index=False)
    (project.map_dir / "clusters.json").write_text(json.dumps(clusters, indent=2, ensure_ascii=False),
                                                   encoding="utf-8")
    # write cluster ids back into the LanceDB papers table
    db = kb.connect(project)
    t = db.open_table(kb.PAPERS)
    rows = t.to_arrow().to_pylist()
    lab = dict(zip(df["paper_id"], df["cluster_id"]))
    for r in rows:
        r["cluster_id"] = int(lab.get(r["paper_id"], -1))
    schema = t.schema
    db.drop_table(kb.PAPERS)
    db.create_table(kb.PAPERS, schema=schema).add(rows)
    return {"papers": len(df), "clusters": len(clusters), "noise": int((labels == -1).sum())}
