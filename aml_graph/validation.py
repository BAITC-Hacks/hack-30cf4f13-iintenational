"""Fail-fast проверки целостности исходных данных."""

from __future__ import annotations

import networkx as nx
import pandas as pd

from .config import (
    EXPECTED_DEPTH_COUNTS,
    EXPECTED_EDGES,
    EXPECTED_NODES,
    EXPECTED_SEEDS,
    EXPECTED_TOTAL_KZT,
    EXPECTED_TRANSACTIONS,
)


class DataValidationError(ValueError):
    """Данные не соответствуют обязательному контракту."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataValidationError(message)


def validate_data(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    transactions: pd.DataFrame,
    graph: nx.DiGraph,
) -> dict[str, object]:
    required = {
        "nodes": ({"gid", "depth", "is_seed"}, nodes),
        "edges": ({"src", "dst", "sum_kzt", "n_tx", "depth"}, edges),
        "transactions": ({"src", "dst", "date", "sum_kzt"}, transactions),
    }
    for name, (columns, frame) in required.items():
        missing = columns - set(frame.columns)
        _require(not missing, f"{name}: отсутствуют колонки {sorted(missing)}")
        _require(not frame[list(columns)].isna().any().any(), f"{name}: есть пустые обязательные значения")

    _require(len(nodes) == EXPECTED_NODES, f"Ожидалось {EXPECTED_NODES} узлов, получено {len(nodes)}")
    _require(len(edges) == EXPECTED_EDGES, f"Ожидалось {EXPECTED_EDGES} рёбер, получено {len(edges)}")
    _require(
        len(transactions) == EXPECTED_TRANSACTIONS,
        f"Ожидалось {EXPECTED_TRANSACTIONS} транзакций, получено {len(transactions)}",
    )
    _require(nodes["gid"].is_unique, "nodes.gid содержит дубли")
    _require(not edges.duplicated(["src", "dst"]).any(), "edges содержит повторные пары src/dst")
    _require(not (edges["src"] == edges["dst"]).any(), "Обнаружены петли src=dst")

    gids = set(nodes["gid"].astype(int))
    edge_gids = set(edges["src"].astype(int)) | set(edges["dst"].astype(int))
    _require(edge_gids <= gids, f"В рёбрах есть неизвестные gid: {sorted(edge_gids - gids)[:5]}")
    _require(int(nodes["is_seed"].sum()) == EXPECTED_SEEDS, "Число seed не равно 81")
    _require(nodes["depth"].between(0, 4).all(), "depth должен быть в диапазоне 0–4")
    _require((edges["depth"] == edges["dst"].map(nodes.set_index("gid")["depth"])).all(), "edges.depth не совпадает с глубиной dst")
    transaction_gids = set(transactions["src"].astype(int)) | set(transactions["dst"].astype(int))
    _require(transaction_gids <= gids, "В transactions есть неизвестные gid")
    _require(set(zip(transactions["src"].astype(int), transactions["dst"].astype(int))) == set(zip(edges["src"].astype(int), edges["dst"].astype(int))), "Пары transactions и edges отличаются")
    _require(pd.to_datetime(transactions["date"]).between("2026-07-01", "2026-07-31").all(), "Есть transactions вне июля 2026")
    depth_counts = nodes.groupby("depth").size().astype(int).to_dict()
    _require(set(depth_counts).issubset({0, 1, 2, 3, 4}), f"Неизвестный уровень depth: {depth_counts}")
    _require(depth_counts == EXPECTED_DEPTH_COUNTS, f"Распределение depth отличается: {depth_counts}")
    _require((edges["sum_kzt"] > 0).all() and (edges["n_tx"] > 0).all(), "Суммы и n_tx должны быть положительными")
    _require((transactions["sum_kzt"] >= 5_000).all(), "Есть транзакции ниже порога 5 000 KZT")
    _require(int(edges["sum_kzt"].sum()) == EXPECTED_TOTAL_KZT, "Оборот edges отличается от контрольного")

    tx_agg = (
        transactions.groupby(["src", "dst"], as_index=False)
        .agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
        .sort_values(["src", "dst"])
        .reset_index(drop=True)
    )
    edge_agg = edges[["src", "dst", "sum_kzt", "n_tx"]].sort_values(["src", "dst"]).reset_index(drop=True)
    _require(tx_agg.equals(edge_agg), "edges не равен агрегации transactions")

    component_sizes = sorted((len(c) for c in nx.weakly_connected_components(graph)), reverse=True)
    _require(len(component_sizes) == 16, f"Ожидалось 16 слабосвязных компонент, получено {len(component_sizes)}")
    _require(component_sizes[:2] == [1_877, 270], f"Размеры двух крупнейших компонент отличаются: {component_sizes[:2]}")
    _require(all(2 <= size <= 17 for size in component_sizes[2:]), f"Размер малой компоненты вне диапазона 2–17: {component_sizes[2:]}")
    depth_four = nodes.loc[nodes["depth"] == 4, "gid"].astype(int)
    depth_four_out = sum(graph.out_degree(gid) for gid in depth_four)
    _require(depth_four_out == 0, "У depth=4 обнаружены исходящие рёбра; проверьте семантику обхода")
    _require(int(transactions["sum_kzt"].sum()) == EXPECTED_TOTAL_KZT, "Оборот transactions отличается от контрольного")

    seed_gids = nodes.loc[nodes["is_seed"], "gid"].astype(int)
    seed_without_out = sum(graph.out_degree(gid) == 0 for gid in seed_gids)
    edge_sources = set(edges["src"].astype(int))
    edge_targets = set(edges["dst"].astype(int))
    seed_absent = sum(gid not in edge_sources and gid not in edge_targets for gid in seed_gids)
    seed_recipient_only = sum(gid not in edge_sources and gid in edge_targets for gid in seed_gids)
    seed_with_outgoing = sum(gid in edge_sources for gid in seed_gids)
    return {
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_transactions": len(transactions),
        "n_seed": int(nodes["is_seed"].sum()),
        "sum_kzt": int(edges["sum_kzt"].sum()),
        "depth_counts": depth_counts,
        "component_sizes": component_sizes,
        "depth4_without_out": int((nodes["depth"] == 4).sum()),
        "seed_without_out": int(seed_without_out),
        "seed_absent_from_edges": int(seed_absent),
        "seed_recipient_only": int(seed_recipient_only),
        "seed_with_outgoing": int(seed_with_outgoing),
    }
