"""Сборка автономного рабочего пространства аналитика из локальных ресурсов."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import networkx as nx
import pandas as pd

from .clustering import build_clusters_table
from .exports import build_nodes_roles, build_top_nodes

ASSET_DIR = Path(__file__).resolve().parents[1] / "web"


def demo_source_matches(data_dir: Path) -> bool:
    """Метка demo действительна только для точных файлов из manifest генератора."""
    try:
        manifest = json.loads((data_dir / "demo_manifest.json").read_text(encoding="utf-8"))
        return isinstance(manifest, dict) and manifest.get("kind") == "synthetic" and all(
            hashlib.sha256((data_dir / name).read_bytes()).hexdigest() == manifest["sha256"][name]
            for name in ("nodes.parquet", "edges.parquet", "transactions.parquet")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _positions(frame: pd.DataFrame) -> dict[int, tuple[float, float]]:
    """Детерминированная раскладка компонент/сообществ, не влияющая на метрики."""
    positions: dict[int, tuple[float, float]] = {}
    layouts = []
    for _, component in frame.groupby("component_id", sort=True):
        groups = list(component.groupby("cluster_id", sort=True))
        columns = max(1, math.ceil(math.sqrt(len(groups))))
        rows = math.ceil(len(groups) / columns)
        layouts.append((groups, columns, columns * 155, rows * 155))
    target_width = max(max(width for _, _, width, _ in layouts),
                       math.sqrt(sum(width * height for _, _, width, height in layouts) * 2))
    offset_x = offset_y = row_height = 0.0
    for groups, columns, width, height in layouts:
        if offset_x and offset_x + width > target_width:
            offset_x = 0.0
            offset_y += row_height + 110
            row_height = 0.0
        for index, (_, group) in enumerate(groups):
            cx = offset_x + (index % columns) * 155
            cy = offset_y + (index // columns) * 155
            ordered = group.sort_values(["depth", "gid"])
            for i, row in enumerate(ordered.itertuples(index=False)):
                angle = i * math.pi * (3 - math.sqrt(5))
                radius = 64 * math.sqrt((i + 0.5) / len(group))
                positions[int(row.gid)] = (round(cx + radius * math.cos(angle), 2), round(cy + radius * math.sin(angle), 2))
        offset_x += width + 110
        row_height = max(row_height, height)
    return positions


def _script_json(value: object) -> str:
    """JSON для script-контекста: данные не могут закрыть тег и создать HTML."""
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def write_graph_view(output_path: Path, graph: nx.DiGraph, frame: pd.DataFrame, *,
                     clusters: pd.DataFrame | None = None, top_nodes: pd.DataFrame | None = None,
                     is_demo: bool = False, period: str = "Период не указан") -> None:
    positions = _positions(frame)
    clusters = build_clusters_table(graph, frame) if clusters is None else clusters
    top_nodes = build_top_nodes(frame) if top_nodes is None else top_nodes
    why_by_gid = top_nodes.set_index("gid")["why"].to_dict()
    nodes = []
    for row in frame.sort_values("gid").to_dict("records"):
        gid = int(row["gid"])
        nodes.append({
            # int64 хранится в JS строкой: точность поиска сохраняется выше 2**53.
            "id": str(gid), "x": positions[gid][0], "y": positions[gid][1],
            "role": str(row["role"]), "roleScore": float(row["role_score"]),
            "priority": float(row["priority_score"]), "cluster": int(row["cluster_id"]),
            "component": int(row["component_id"]), "depth": int(row["depth"]),
            "seed": bool(row["is_seed"]), "inDegree": int(row["in_degree"]),
            "outDegree": int(row["out_degree"]), "sumIn": int(row["sum_in"]),
            "sumOut": int(row["sum_out"]), "evidence": str(row["evidence"]),
            "why": str(why_by_gid.get(gid, "")),
            "centrality": float(max(row["pagerank_component_pct"], row["betweenness_component_pct"])),
            "turnoverPercentile": float(row["turnover_global_pct"]),
        })
    edges = [{"s": str(src), "t": str(dst), "v": int(data["sum_kzt"]), "count": int(data["n_tx"])}
             for src, dst, data in graph.edges(data=True)]
    csv_frames = {"nodes_roles.csv": build_nodes_roles(frame), "clusters.csv": clusters, "top_nodes.csv": top_nodes}
    payload = {
        "nodes": nodes, "edges": edges,
        "clusters": [{"id": int(row.cluster_id), "size": int(row.n_nodes), "seeds": int(row.n_seed),
                      "amount": int(row.sum_kzt_internal), "hypothesis": str(row.hypothesis)}
                     for row in clusters.itertuples(index=False)],
        "meta": {"demo": is_demo, "period": period, "total": sum(edge["v"] for edge in edges),
                 "components": int(frame["component_id"].nunique()), "seeds": int(frame["is_seed"].sum())},
        "downloads": {name: table.to_csv(index=False, lineterminator="\n") for name, table in csv_frames.items()},
    }
    document = (ASSET_DIR / "index.html").read_text(encoding="utf-8")
    document = document.replace("/*__STYLE__*/", (ASSET_DIR / "styles.css").read_text(encoding="utf-8"))
    document = document.replace("/*__SCRIPT__*/", (ASSET_DIR / "app.js").read_text(encoding="utf-8"))
    document = document.replace("__DATA_JSON__", _script_json(payload))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
