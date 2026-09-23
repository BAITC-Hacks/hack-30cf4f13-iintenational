"""Сборка ориентированного графа из валидированных таблиц."""

import networkx as nx
import pandas as pd


def build_graph(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    # NetworkX samples nodes and walks neighbours in insertion order. A fixed
    # random seed alone does not make centralities/Louvain independent of the
    # order in which the same source rows happen to be stored.
    for row in nodes.sort_values("gid").itertuples(index=False):
        graph.add_node(int(row.gid), depth=int(row.depth), is_seed=bool(row.is_seed))
    for row in edges.sort_values(["src", "dst"]).itertuples(index=False):
        graph.add_edge(
            int(row.src),
            int(row.dst),
            sum_kzt=int(row.sum_kzt),
            n_tx=int(row.n_tx),
            depth=int(row.depth),
        )
    return graph
