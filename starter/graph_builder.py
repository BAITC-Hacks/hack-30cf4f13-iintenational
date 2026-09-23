"""Сборка ориентированного графа из валидированных таблиц."""

import networkx as nx
import pandas as pd


def build_graph(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    for row in nodes.itertuples(index=False):
        graph.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    for row in edges.itertuples(index=False):
        graph.add_edge(
            int(row.src),
            int(row.dst),
            sum_kzt=int(row.sum_kzt),
            n_tx=int(row.n_tx),
            depth=int(row.depth),
        )
    return graph

