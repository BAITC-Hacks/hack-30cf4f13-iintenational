"""Базовые прозрачные метрики узлов."""

import networkx as nx
import pandas as pd


def calculate_basic_metrics(graph: nx.DiGraph) -> pd.DataFrame:
    rows: list[dict[str, int | float | bool]] = []
    for gid, attrs in graph.nodes(data=True):
        sum_in = sum(int(data["sum_kzt"]) for _, _, data in graph.in_edges(gid, data=True))
        sum_out = sum(int(data["sum_kzt"]) for _, _, data in graph.out_edges(gid, data=True))
        ratio = float(sum_out / sum_in) if sum_in else float("nan")
        rows.append(
            {
                "gid": int(gid),
                "depth": int(attrs["depth"]),
                "is_seed": bool(attrs["is_seed"]),
                "in_degree": int(graph.in_degree(gid)),
                "out_degree": int(graph.out_degree(gid)),
                "unique_payers": int(graph.in_degree(gid)),
                "unique_recipients": int(graph.out_degree(gid)),
                "sum_in": int(sum_in),
                "sum_out": int(sum_out),
                "pass_through_ratio": ratio,
            }
        )
    return pd.DataFrame(rows).sort_values("gid").reset_index(drop=True)

