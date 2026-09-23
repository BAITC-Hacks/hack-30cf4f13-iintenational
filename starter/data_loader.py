"""Загрузка parquet-файлов с единым приведением типов."""

from pathlib import Path

import pandas as pd

from aml_graph.validation import validate_schema


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Загрузить nodes, edges и transactions из каталога ``data_dir``."""
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    edges = pd.read_parquet(data_dir / "edges.parquet")
    transactions = pd.read_parquet(data_dir / "transactions.parquet")

    validate_schema(nodes, edges, transactions)
    for frame, columns in (
        (nodes, ("gid", "depth")),
        (edges, ("src", "dst", "sum_kzt", "n_tx", "depth")),
        (transactions, ("src", "dst", "sum_kzt")),
    ):
        for column in columns:
            frame[column] = frame[column].astype("int64")
    nodes["is_seed"] = nodes["is_seed"].astype(bool)
    transactions["date"] = pd.to_datetime(transactions["date"])
    return nodes, edges, transactions
