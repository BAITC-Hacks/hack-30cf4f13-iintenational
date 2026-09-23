"""Базовые и компонентно-нормированные графовые метрики."""

from __future__ import annotations

import math

import networkx as nx
import pandas as pd

from starter.basic_metrics import calculate_basic_metrics


def _weighted_pagerank(graph: nx.DiGraph, damping: float = 0.85, max_iter: int = 100, tol: float = 1e-10) -> dict[int, float]:
    """Небольшая реализация PageRank без зависимости от SciPy."""
    nodes = list(graph.nodes())
    count = len(nodes)
    if count == 0:
        return {}
    rank = {node: 1.0 / count for node in nodes}
    outgoing_weight = {
        node: sum(float(data.get("sum_kzt", 1.0)) for _, _, data in graph.out_edges(node, data=True))
        for node in nodes
    }
    base = (1.0 - damping) / count
    for _ in range(max_iter):
        dangling = damping * sum(rank[node] for node in nodes if outgoing_weight[node] == 0.0) / count
        updated = {node: base + dangling for node in nodes}
        for source, target, data in graph.edges(data=True):
            updated[target] += damping * rank[source] * float(data.get("sum_kzt", 1.0)) / outgoing_weight[source]
        error = sum(abs(updated[node] - rank[node]) for node in nodes)
        rank = updated
        if error < tol:
            break
    return rank


def calculate_metrics(graph: nx.DiGraph) -> pd.DataFrame:
    metrics = calculate_basic_metrics(graph).set_index("gid")
    components = sorted(nx.weakly_connected_components(graph), key=lambda value: (-len(value), min(value)))

    component_by_gid: dict[int, int] = {}
    component_size_by_gid: dict[int, int] = {}
    pagerank: dict[int, float] = {}
    betweenness: dict[int, float] = {}

    for component_id, members in enumerate(components):
        subgraph = graph.subgraph(members).copy()
        for gid in members:
            component_by_gid[int(gid)] = component_id
            component_size_by_gid[int(gid)] = len(members)
        pagerank.update(_weighted_pagerank(subgraph))
        sample_size = None if len(members) <= 300 else min(128, len(members))
        betweenness.update(
            nx.betweenness_centrality(
                subgraph,
                k=sample_size,
                normalized=True,
                weight=None,
                seed=42,
            )
        )

    metrics["component_id"] = pd.Series(component_by_gid)
    metrics["component_size"] = pd.Series(component_size_by_gid)
    metrics["pagerank"] = pd.Series(pagerank)
    metrics["betweenness"] = pd.Series(betweenness)
    metrics["turnover"] = metrics["sum_in"] + metrics["sum_out"]
    metrics["retained_observed"] = (metrics["sum_in"] - metrics["sum_out"]).clip(lower=0)
    metrics["retention_ratio"] = metrics["retained_observed"] / metrics["sum_in"].replace(0, math.nan)

    for column in ("pagerank", "betweenness", "turnover"):
        metrics[f"{column}_component_pct"] = metrics.groupby("component_id")[column].rank(
            method="average", pct=True
        )
    metrics["turnover_global_pct"] = metrics["turnover"].rank(method="average", pct=True)
    return metrics.reset_index().sort_values("gid").reset_index(drop=True)


def candidate_summary(metrics: pd.DataFrame) -> dict[str, object]:
    collectors = metrics.loc[metrics["unique_payers"].between(8, 24), ["gid", "unique_payers"]]
    fans = metrics.loc[metrics["unique_recipients"].between(60, 116), ["gid", "unique_recipients"]]
    transit = metrics.loc[metrics["pass_through_ratio"].between(0.8, 1.2), ["gid", "pass_through_ratio"]]
    return {
        "collectors_8_24": int(len(collectors)),
        "collector_examples": collectors.sort_values("unique_payers", ascending=False).head(5).to_dict("records"),
        "fans_60_116": int(len(fans)),
        "fan_examples": fans.sort_values("unique_recipients", ascending=False).head(5).to_dict("records"),
        "pass_through_08_12": int(len(transit)),
        "transit_examples": transit.head(5).to_dict("records"),
    }
