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


def validate_schema(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    transactions: pd.DataFrame,
) -> None:
    """Проверить исходные типы до любого потенциально потерянного приведения.

    ``astype(int64)`` усекал дробные значения, а ``astype(bool)`` превращал
    непустую строку ``"False"`` в True. Ошибки схемы должны останавливать запуск.
    """
    required = {
        "nodes": ({"gid", "depth", "is_seed"}, nodes),
        "edges": ({"src", "dst", "sum_kzt", "n_tx", "depth"}, edges),
        "transactions": ({"src", "dst", "date", "sum_kzt"}, transactions),
    }
    for name, (columns, frame) in required.items():
        _require(frame.columns.is_unique, f"{name}: повторяющиеся имена колонок")
        missing = columns - set(frame.columns)
        _require(not missing, f"{name}: отсутствуют колонки {sorted(missing)}")
        _require(not frame[list(columns)].isna().any().any(), f"{name}: есть пустые обязательные значения")
        for column in columns - {"is_seed", "date"}:
            values = frame[column]
            _require(pd.api.types.is_integer_dtype(values.dtype), f"{name}.{column}: ожидается целочисленный тип")
            _require(values.between(-(2**63), 2**63 - 1).all(), f"{name}.{column}: значение вне диапазона int64")
    _require(pd.api.types.is_bool_dtype(nodes["is_seed"].dtype), "nodes.is_seed: ожидается bool (True/False)")
    _require(pd.api.types.is_datetime64_any_dtype(transactions["date"].dtype), "transactions.date: ожидается datetime64")
    _require(transactions["date"].dt.tz is None, "transactions.date: ожидается дата без часового пояса")


def validate_data(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    transactions: pd.DataFrame,
    graph: nx.DiGraph,
) -> dict[str, object]:
    validate_schema(nodes, edges, transactions)

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
    _require((nodes["is_seed"] == nodes["depth"].eq(0)).all(), "is_seed должен совпадать с depth=0")
    _require((edges["depth"] == edges["dst"].map(nodes.set_index("gid")["depth"])).all(), "edges.depth не совпадает с глубиной dst")
    transaction_gids = set(transactions["src"].astype(int)) | set(transactions["dst"].astype(int))
    _require(transaction_gids <= gids, "В transactions есть неизвестные gid")
    _require(set(zip(transactions["src"].astype(int), transactions["dst"].astype(int))) == set(zip(edges["src"].astype(int), edges["dst"].astype(int))), "Пары transactions и edges отличаются")
    dates = transactions["date"]
    _require(((dates >= "2026-07-01") & (dates < "2026-08-01")).all(), "Есть transactions вне июля 2026")
    depth_counts = nodes.groupby("depth").size().astype(int).to_dict()
    _require(set(depth_counts).issubset({0, 1, 2, 3, 4}), f"Неизвестный уровень depth: {depth_counts}")
    _require(depth_counts == EXPECTED_DEPTH_COUNTS, f"Распределение depth отличается: {depth_counts}")
    _require((edges["sum_kzt"] > 0).all() and (edges["n_tx"] > 0).all(), "Суммы и n_tx должны быть положительными")
    _require((transactions["sum_kzt"] >= 5_000).all(), "Есть транзакции ниже порога 5 000 KZT")
    # Convert each value before summing: int(pandas.sum()) is already too late
    # after int64 overflow. With positive amounts and both exact totals checked
    # here, every subsequent group sum is bounded by EXPECTED_TOTAL_KZT.
    edge_total = sum(map(int, edges["sum_kzt"]))
    transaction_total = sum(map(int, transactions["sum_kzt"]))
    _require(edge_total == EXPECTED_TOTAL_KZT, "Оборот edges отличается от контрольного")
    _require(transaction_total == EXPECTED_TOTAL_KZT, "Оборот transactions отличается от контрольного")

    tx_agg = (
        transactions.groupby(["src", "dst"], as_index=False)
        .agg(sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size"))
        .sort_values(["src", "dst"])
        .reset_index(drop=True)
    )
    edge_agg = edges[["src", "dst", "sum_kzt", "n_tx"]].sort_values(["src", "dst"]).reset_index(drop=True)
    _require(tx_agg.equals(edge_agg), "edges не равен агрегации transactions")

    components = sorted(nx.weakly_connected_components(graph), key=lambda members: (-len(members), min(members)))
    component_sizes = [len(members) for members in components]
    _require(len(component_sizes) == 16, f"Ожидалось 16 слабосвязных компонент, получено {len(component_sizes)}")
    _require(component_sizes[:2] == [1_877, 270], f"Размеры двух крупнейших компонент отличаются: {component_sizes[:2]}")
    _require(all(2 <= size <= 17 for size in component_sizes[2:]), f"Размер малой компоненты вне диапазона 2–17: {component_sizes[2:]}")
    seed_gids = set(nodes.loc[nodes["is_seed"], "gid"].astype(int))
    component_seed_counts = [len(members & seed_gids) for members in components]
    _require(
        component_seed_counts[:2] == [46, 1],
        f"Число seed двух крупнейших компонент отличается от [46, 1]: {component_seed_counts[:2]}",
    )
    depth_four = nodes.loc[nodes["depth"] == 4, "gid"].astype(int)
    depth_four_out = sum(graph.out_degree(gid) for gid in depth_four)
    _require(depth_four_out == 0, "У depth=4 обнаружены исходящие рёбра; проверьте семантику обхода")

    seed_without_out = sum(graph.out_degree(gid) == 0 for gid in seed_gids)
    edge_sources = set(edges["src"].astype(int))
    edge_targets = set(edges["dst"].astype(int))
    seed_absent = sum(gid not in edge_sources and gid not in edge_targets for gid in seed_gids)
    seed_recipient_only = sum(gid not in edge_sources and gid in edge_targets for gid in seed_gids)
    seed_with_outgoing = sum(gid in edge_sources for gid in seed_gids)
    nodes_out_gt_in = sum(
        sum(int(data["sum_kzt"]) for _, _, data in graph.out_edges(gid, data=True))
        > sum(int(data["sum_kzt"]) for _, _, data in graph.in_edges(gid, data=True))
        for gid in graph
    )
    return {
        "n_nodes": len(nodes),
        "n_edges": len(edges),
        "n_transactions": len(transactions),
        "n_seed": int(nodes["is_seed"].sum()),
        "sum_kzt": edge_total,
        "depth_counts": depth_counts,
        "component_sizes": component_sizes,
        "component_seed_counts": component_seed_counts,
        "outside_largest_component": len(nodes) - component_sizes[0],
        "nodes_out_gt_in": nodes_out_gt_in,
        "depth4_without_out": int((nodes["depth"] == 4).sum()),
        "seed_without_out": int(seed_without_out),
        "seed_absent_from_edges": int(seed_absent),
        "seed_recipient_only": int(seed_recipient_only),
        "seed_with_outgoing": int(seed_with_outgoing),
    }
