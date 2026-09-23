"""Точные обязательные схемы выходных CSV."""

NODES_ROLES_COLUMNS = [
    "gid",
    "role",
    "role_score",
    "cluster_id",
    "priority_score",
    "evidence",
]

CLUSTERS_COLUMNS = [
    "cluster_id",
    "n_nodes",
    "n_seed",
    "sum_kzt_internal",
    "top_gids",
    "hypothesis",
]

TOP_NODES_COLUMNS = ["rank", "gid", "role", "priority_score", "why"]

