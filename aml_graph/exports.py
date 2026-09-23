"""Формирование и acceptance-проверки трёх обязательных CSV."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from starter.export_templates import CLUSTERS_COLUMNS, NODES_ROLES_COLUMNS, TOP_NODES_COLUMNS

from .config import EXPECTED_NODES, TOP_N
from .roles import ROLES


def build_nodes_roles(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame[NODES_ROLES_COLUMNS].copy()
    result["role_score"] = result["role_score"].round(6)
    result["priority_score"] = result["priority_score"].round(6)
    return result.sort_values("gid").reset_index(drop=True)


def build_top_nodes(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = frame.sort_values(
        ["priority_score", "role_score", "gid"], ascending=[False, False, True]
    ).head(TOP_N).copy()
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    centrality = ranked[["betweenness_component_pct", "pagerank_component_pct"]].max(axis=1)
    ranked["why"] = [
        (
            f"Приоритет проверки: роль {role}, сила правила {role_score:.0%}; "
            f"центральность выше {cent:.0%} узлов своей компоненты, оборот выше {turn:.0%} узлов графа."
        )
        for role, role_score, cent, turn in zip(
            ranked["role"], ranked["role_score"], centrality, ranked["turnover_global_pct"], strict=True
        )
    ]
    ranked["priority_score"] = ranked["priority_score"].round(6)
    return ranked[TOP_NODES_COLUMNS].reset_index(drop=True)


def validate_outputs(nodes_roles: pd.DataFrame, clusters: pd.DataFrame, top_nodes: pd.DataFrame) -> None:
    if list(nodes_roles.columns) != NODES_ROLES_COLUMNS:
        raise AssertionError("Неверная схема nodes_roles.csv")
    if list(clusters.columns) != CLUSTERS_COLUMNS:
        raise AssertionError("Неверная схема clusters.csv")
    if list(top_nodes.columns) != TOP_NODES_COLUMNS:
        raise AssertionError("Неверная схема top_nodes.csv")
    if len(nodes_roles) != EXPECTED_NODES or not nodes_roles["gid"].is_unique:
        raise AssertionError("nodes_roles.csv должен содержать 2 248 уникальных узла")
    if nodes_roles.isna().any().any() or (nodes_roles["evidence"].str.strip() == "").any():
        raise AssertionError("В nodes_roles.csv есть пустые обязательные поля")
    if not set(nodes_roles["role"]).issubset(ROLES):
        raise AssertionError("nodes_roles.csv содержит неизвестную роль")
    if not nodes_roles["role_score"].between(0, 1).all() or not nodes_roles["priority_score"].between(0, 1).all():
        raise AssertionError("Скоры должны лежать в диапазоне 0–1")
    if (nodes_roles["evidence"].str.len() > 200).any():
        raise AssertionError("evidence длиннее 200 символов")
    if set(nodes_roles["cluster_id"]) != set(clusters["cluster_id"]):
        raise AssertionError("cluster_id не согласованы между выгрузками")
    if clusters.isna().any().any() or (clusters["hypothesis"].astype(str).str.strip() == "").any():
        raise AssertionError("В clusters.csv есть пустые обязательные поля")
    if int(clusters["n_nodes"].sum()) != EXPECTED_NODES:
        raise AssertionError("Размеры кластеров не покрывают все узлы")
    if len(top_nodes) < 20 or top_nodes.isna().any().any() or (top_nodes["why"].str.strip() == "").any():
        raise AssertionError("top_nodes.csv должен содержать минимум 20 обоснованных строк")
    if not top_nodes["priority_score"].between(0, 1).all():
        raise AssertionError("priority_score в top_nodes.csv вне диапазона 0–1")


def write_outputs(output_dir: Path, nodes_roles: pd.DataFrame, clusters: pd.DataFrame, top_nodes: pd.DataFrame) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_roles.to_csv(output_dir / "nodes_roles.csv", index=False, encoding="utf-8-sig")
    clusters.to_csv(output_dir / "clusters.csv", index=False, encoding="utf-8-sig")
    top_nodes.to_csv(output_dir / "top_nodes.csv", index=False, encoding="utf-8-sig")
