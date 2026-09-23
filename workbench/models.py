"""Контракты модулей локального приложения и явные пределы первой версии."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 128
MAX_CELL_CHARS = 4096
MAX_NODES = 10_000
MAX_EDGES = 50_000
FORMATS = (".csv", ".tsv", ".xlsx", ".json", ".jsonl", ".parquet")


class ImportFailure(ValueError):
    """Понятная ошибка данных; issues не содержат содержимого исходных ячеек."""

    def __init__(self, message: str, issues: list[dict] | None = None):
        super().__init__(message)
        self.issues = (issues or [])[:100]


@dataclass
class Dataset:
    """Строковые gid; amount_minor — точное целое, отсутствующие факты — null."""

    nodes: pd.DataFrame
    edges: pd.DataFrame
    transactions: pd.DataFrame | None
    metadata: dict
    warnings: list[dict] = field(default_factory=list)
