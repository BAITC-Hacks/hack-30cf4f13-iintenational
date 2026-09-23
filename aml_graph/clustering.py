"""Louvain отдельно внутри каждой слабосвязной компоненты."""

from __future__ import annotations

import math

import networkx as nx
import pandas as pd


def detect_clusters(graph: nx.DiGraph) -> dict[int, int]:
    components = sorted(nx.weakly_connected_components(graph), key=lambda value: (-len(value), min(value)))
    cluster_by_gid: dict[int, int] = {}
    next_cluster_id = 0

    for members in components:
        subgraph = graph.subgraph(members)
        undirected = nx.Graph()
        undirected.add_nodes_from(subgraph.nodes())
        for source, target, data in subgraph.edges(data=True):
            weight = math.log1p(float(data.get("sum_kzt", 1)))
            if undirected.has_edge(source, target):
                undirected[source][target]["cluster_weight"] += weight
            else:
                undirected.add_edge(source, target, cluster_weight=weight)

        if len(undirected) <= 2 or undirected.number_of_edges() == 0:
            communities = [set(undirected.nodes())]
        else:
            communities = nx.community.louvain_communities(
                undirected,
                weight="cluster_weight",
                resolution=1.0,
                seed=42,
            )
        communities = sorted(communities, key=lambda value: min(value))
        for community in communities:
            for gid in community:
                cluster_by_gid[int(gid)] = next_cluster_id
            next_cluster_id += 1

    if len(cluster_by_gid) != graph.number_of_nodes():
        raise AssertionError("Не каждый узел получил cluster_id")
    return cluster_by_gid


def _hypothesis(group: pd.DataFrame) -> str:
    counts = group["role"].value_counts().to_dict()
    n_seed = int(group["is_seed"].sum())
    notable = counts.get("coordinator", 0) + counts.get("consolidator", 0) + counts.get("distributor", 0)
    if n_seed > 1 and notable:
        return "Гипотеза для проверки: связанное сообщество нескольких seed с выраженными узлами управления потоками."
    if counts.get("distributor", 0):
        return "Гипотеза для проверки: сообщество с признаками веерного распределения средств."
    if counts.get("consolidator", 0):
        return "Гипотеза для проверки: сообщество с признаками консолидации входящих потоков."
    if counts.get("transit", 0) >= max(2, len(group) // 5):
        return "Гипотеза для проверки: сообщество с заметной долей транзитных профилей."
    if (group["depth"] == 4).mean() >= 0.5:
        return "Гипотеза для проверки: пограничный фрагмент; выводы ограничены обрывом обхода на depth=4."
    return "Гипотеза для проверки: локальная группа связей без одного доминирующего структурного паттерна."


def build_clusters_table(graph: nx.DiGraph, frame: pd.DataFrame) -> pd.DataFrame:
    cluster_by_gid = frame.set_index("gid")["cluster_id"].astype(int).to_dict()
    internal_sum: dict[int, int] = {int(value): 0 for value in frame["cluster_id"].unique()}
    for source, target, data in graph.edges(data=True):
        cluster_id = cluster_by_gid[int(source)]
        if cluster_id == cluster_by_gid[int(target)]:
            internal_sum[cluster_id] += int(data["sum_kzt"])

    rows: list[dict[str, object]] = []
    for cluster_id, group in frame.groupby("cluster_id", sort=True):
        top = group.sort_values(
            ["turnover_component_pct", "betweenness_component_pct", "gid"],
            ascending=[False, False, True],
        ).head(5)
        rows.append(
            {
                "cluster_id": int(cluster_id),
                "n_nodes": int(len(group)),
                "n_seed": int(group["is_seed"].sum()),
                "sum_kzt_internal": int(internal_sum[int(cluster_id)]),
                "top_gids": ",".join(str(int(gid)) for gid in top["gid"]),
                "hypothesis": _hypothesis(group),
            }
        )
    return pd.DataFrame(rows)

