"""Два явных профиля поверх общего объяснимого графового ядра.

Внутренние целые номера generic-узлов — только технический ключ NetworkX;
публичные артефакты сохраняют исходные строковые идентификаторы.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import tempfile
import time
from pathlib import Path

import networkx as nx
import pandas as pd

from aml_graph.clustering import build_clusters_table, detect_clusters
from aml_graph.config import (
    CONSOLIDATOR_MIN_PAYERS, COORDINATOR_CENTRALITY_PERCENTILE,
    DISTRIBUTOR_MIN_RECIPIENTS, ROLE_BASE_PRIORITY, TRANSIT_RATIO_HIGH,
    TRANSIT_RATIO_LOW,
)
from aml_graph.exports import build_nodes_roles, build_top_nodes, write_outputs
from aml_graph.metrics import calculate_metrics
from aml_graph.pipeline import run_pipeline
from aml_graph.roles import assign_roles, calculate_priority
from aml_graph.validation import DataValidationError, validate_data
from aml_graph.visualization import write_graph_view
from starter.graph_builder import build_graph

from .importing import normalize_config, prepare_dataset
from .models import Dataset, ImportFailure

RULES_VERSION = "explainable-1.1"
SCHEMA_VERSION = "workbench-1"
ARTIFACTS = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "report.json", "result.json", "graph_view.html")


def _money(value: int, decimals: int) -> str:
    """Точный текст без преобразования денег в float."""
    if decimals == 0:
        return str(int(value))
    whole, fraction = divmod(int(value), 10 ** decimals)
    return f"{whole}.{fraction:0{decimals}d}"


def _safe_csv_id(value: str) -> str:
    # Canonical result.json и UI остаются без изменений. CSV предназначен для
    # ручного открытия; префикс ' отключает формулу, не использует ="...".
    # Исходный начальный апостроф тоже экранируется: =foo и '=foo должны
    # оставаться разными ID. Для декодирования достаточно снять один префикс.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "'")) or value.startswith(("\t", "\r", "\n")) else value


def _provenance(sources: list[dict]) -> list[dict]:
    records = []
    for source in sources:
        path = Path(source["path"])
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append({"kind": source["kind"], "filename": str(source.get("filename", path.name)),
                        "sha256": digest, "bytes": path.stat().st_size,
                        "mapping": source.get("mapping", {}), "options": source.get("options", {})})
    return records


def _strict_tables(dataset: Dataset) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if dataset.transactions is None:
        raise ImportFailure("Профиль HackAlem требует таблицу transactions.")
    nodes = dataset.nodes.copy()
    edges = dataset.edges.rename(columns={"amount_minor": "sum_kzt"}).copy()
    transactions = dataset.transactions.rename(columns={"amount_minor": "sum_kzt"}).copy()
    tables = {"nodes": (nodes, ("gid", "depth", "is_seed")),
              "edges": (edges, ("src", "dst", "sum_kzt", "n_tx", "depth")),
              "transactions": (transactions, ("src", "dst", "sum_kzt", "date"))}
    for name, (frame, fields) in tables.items():
        for field in fields:
            if field not in frame or frame[field].isna().any():
                raise ImportFailure(f"HackAlem: заполните {name}.{field}.",
                                    [{"table": name, "row": None, "field": field, "message": "Обязательное поле HackAlem отсутствует или пусто."}])
        for field in set(fields) - {"is_seed", "date"}:
            if field in {"gid", "src", "dst"}:
                valid = frame[field].map(lambda item: bool(re.fullmatch(r"-?(0|[1-9][0-9]*)", str(item))))
                if not valid.all():
                    raise ImportFailure(f"HackAlem: {name}.{field} должен представлять int64 без ведущих нулей.")
            try:
                values = [int(value) for value in frame[field]]
                if any(not -(2**63) <= value < 2**63 for value in values):
                    raise OverflowError
                frame[field] = pd.Series(values, dtype="int64", index=frame.index)
            except (OverflowError, ValueError, TypeError) as exc:
                raise ImportFailure(f"HackAlem: {name}.{field} вне диапазона int64.") from exc
    nodes["is_seed"] = nodes["is_seed"].astype(bool)
    transactions["date"] = pd.to_datetime(transactions["date"])
    return nodes[["gid", "depth", "is_seed"]], edges[["src", "dst", "sum_kzt", "n_tx", "depth"]], transactions[["src", "dst", "date", "sum_kzt"]]


def _generic_graph(dataset: Dataset) -> tuple[nx.DiGraph, dict[int, str], dict[int, dict]]:
    ids = sorted(str(value) for value in dataset.nodes["gid"])
    internal = {value: index for index, value in enumerate(ids)}
    display = {index: value for value, index in internal.items()}
    facts: dict[int, dict] = {}
    graph = nx.DiGraph()
    # Centrality sampling and Louvain consume NetworkX insertion order, too.
    # Stable IDs alone are insufficient when the uploaded nodes are shuffled.
    for row in dataset.nodes.sort_values("gid").itertuples(index=False):
        gid = internal[str(row.gid)]
        depth = None if pd.isna(row.depth) else int(row.depth)
        seed = None if pd.isna(row.is_seed) else bool(row.is_seed)
        facts[gid] = {"depth": depth, "seed": seed}
        # starter.basic_metrics requires scalar int/bool. These implementation
        # sentinels are removed immediately after calculation, before any rule.
        graph.add_node(gid, depth=-1 if depth is None else depth, is_seed=False if seed is None else seed)
    for row in dataset.edges.sort_values(["src", "dst"]).itertuples(index=False):
        graph.add_edge(internal[str(row.src)], internal[str(row.dst)], sum_kzt=int(row.amount_minor),
                       n_tx=None if pd.isna(row.n_tx) else int(row.n_tx))
    return graph, display, facts


def _capabilities(dataset: Dataset, config: dict, *, completed: bool = False) -> list[dict]:
    available = "completed" if completed else "available"
    known_seeds = bool(dataset.nodes["is_seed"].notna().all())
    flow_count = 0
    if config["coverage"] == "outward":
        eligible = (dataset.nodes["depth"].notna() & dataset.nodes["is_seed"].notna()
                    & dataset.nodes["depth"].lt(config["max_depth"])
                    & dataset.nodes["is_seed"].eq(False))
        flow_count = int(eligible.fillna(False).sum())
    return [
        {"id": "structural_roles", "status": available, "reason": "Степени, наблюдаемые суммы и центральность по компонентам доступны."},
        {"id": "flow_roles", "status": available if flow_count else "unavailable",
         "reason": f"Правила transit/terminal допустимы для {flow_count} внутренних non-seed узлов объявленного обхода." if flow_count else "Нет узлов с подтверждённой внутренней глубиной и non-seed: transit/terminal отключены."},
        {"id": "clustering", "status": available, "reason": "Louvain отдельно по слабосвязным компонентам; изолированные узлы сохранены."},
        {"id": "ranking", "status": available, "reason": f"Объяснимый top-{min(config['top_n'], len(dataset.nodes))}; скор — не вероятность нарушения."},
        {"id": "seed_context", "status": available if known_seeds else "limited", "reason": "Признак seed известен для всех узлов." if known_seeds else "Неизвестные seed остаются null; полный n_seed таких кластеров не оценивается."},
        {"id": "temporal_analysis", "status": "unavailable", "reason": "Временные паттерны не входят в эту версию; наличие дат не означает выполнения такого анализа."},
    ]


def _preview(dataset: Dataset, config: dict, graph: nx.DiGraph) -> dict:
    seed_count = None if dataset.nodes["is_seed"].isna().any() else int(dataset.nodes["is_seed"].sum())
    return {"profile": config["profile"], "currency": config["currency"], "decimals": config["decimals"],
            "amount_total": _money(sum(int(value) for value in dataset.edges["amount_minor"]), config["decimals"]),
            "amount_total_minor": str(sum(int(value) for value in dataset.edges["amount_minor"])),
            "counts": {"nodes": len(dataset.nodes), "edges": len(dataset.edges),
                       "transactions": None if dataset.transactions is None else len(dataset.transactions),
                       "components": nx.number_weakly_connected_components(graph), "seeds": seed_count},
            "warnings": list(dataset.warnings), "capabilities": _capabilities(dataset, config), "config": config}


def _prepare(sources: list[dict], raw_config: dict) -> tuple[Dataset, dict, nx.DiGraph, dict, dict, tuple | None]:
    config = normalize_config(raw_config)
    dataset = prepare_dataset(sources, config)
    if config["profile"] == "hackalem":
        if {source["kind"] for source in sources} != {"nodes", "edges", "transactions"}:
            raise ImportFailure("HackAlem требует три отдельные таблицы: nodes, edges, transactions.")
        try:
            tables = _strict_tables(dataset)
            graph = build_graph(tables[0], tables[1])
            validate_data(*tables, graph)
        except (DataValidationError, OverflowError) as exc:
            raise ImportFailure(f"Контроль HackAlem: {exc}") from exc
        return dataset, config, graph, {}, {}, tables
    graph, display, facts = _generic_graph(dataset)
    return dataset, config, graph, display, facts, None


def validate_request(sources: list[dict], config: dict) -> dict:
    dataset, config, graph, _, _, _ = _prepare(sources, config)
    return _preview(dataset, config, graph)


def _period(dataset: Dataset) -> str:
    if dataset.transactions is None or "date" not in dataset.transactions:
        return "Даты не предоставлены"
    dates = dataset.transactions["date"].dropna()
    if dates.empty:
        return "Даты не предоставлены"
    prefix = "Часть дат: " if len(dates) != len(dataset.transactions) else ""
    return f"{prefix}{dates.min():%d.%m.%Y} — {dates.max():%d.%m.%Y}"


def _generic_outputs(dataset: Dataset, config: dict, graph: nx.DiGraph, display: dict,
                     facts: dict, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame = calculate_metrics(graph)
    frame["depth"] = pd.array([facts[int(gid)]["depth"] for gid in frame["gid"]], dtype="Int64")
    frame["is_seed"] = pd.array([facts[int(gid)]["seed"] for gid in frame["gid"]], dtype="boolean")
    frame = assign_roles(frame, coverage=config["coverage"], max_depth=config["max_depth"],
                         currency=config["currency"], decimals=config["decimals"])
    frame["cluster_id"] = frame["gid"].map(detect_clusters(graph)).astype(int)
    boundary_depth = config["max_depth"] if config["coverage"] == "outward" else None
    frame = calculate_priority(frame, boundary_depth=boundary_depth)
    clusters = build_clusters_table(graph, frame, boundary_depth=boundary_depth)
    clusters["n_seed"] = clusters["n_seed"].astype("Int64")
    top_nodes = build_top_nodes(frame, top_n=config["top_n"])
    nodes_roles = build_nodes_roles(frame)
    public_nodes = nodes_roles.copy()
    public_nodes["gid"] = public_nodes["gid"].map(display).map(_safe_csv_id)
    public_top = top_nodes.copy()
    public_top["gid"] = public_top["gid"].map(display).map(_safe_csv_id)
    public_clusters = clusters.copy()
    public_clusters["top_gids"] = public_clusters["top_gids"].map(
        lambda value: json.dumps([display[int(item)] for item in value.split(",")], ensure_ascii=False))
    public_clusters["amount_internal"] = public_clusters.pop("sum_kzt_internal").map(lambda value: _money(value, config["decimals"]))
    public_clusters["currency"] = config["currency"]
    public_clusters = public_clusters[["cluster_id", "n_nodes", "n_seed", "amount_internal", "currency", "top_gids", "hypothesis"]]
    write_outputs(output_dir, public_nodes, public_clusters, public_top)
    for gid, node_facts in facts.items():
        node_facts["boundary"] = (
            node_facts["depth"] == boundary_depth if boundary_depth is not None and node_facts["depth"] is not None else None)
    description = (
        "0,45 × вес роли + 0,25 × сила правила + 0,20 × максимальный процентиль "
        "PageRank/betweenness в компоненте + 0,10 × процентиль оборота в графе "
        "(процентили в формуле — по шкале 0–1)."
    )
    if boundary_depth is not None:
        description += f" Для depth={boundary_depth} при out_degree=0 итог умножается на 0,85."
    write_graph_view(
        output_dir / "graph_view.html", graph, frame, clusters=clusters, top_nodes=top_nodes,
        display_ids=display, node_overrides=facts, period=_period(dataset),
        download_frames={"nodes_roles.csv": public_nodes, "clusters.csv": public_clusters, "top_nodes.csv": public_top},
        metadata={"profile": "generic", "currency": config["currency"], "moneyScale": config["decimals"],
                  "coverage": config["coverage"], "maxDepth": boundary_depth,
                  "seedKnown": bool(dataset.nodes["is_seed"].notna().all()),
                  "notice": "Пользовательский набор. Выводы — гипотезы по предоставленной сети; полный баланс и внешние потоки неизвестны.",
                  "priorityDescription": description},
    )
    return frame, public_clusters, public_top


def run_analysis(sources: list[dict], config: dict, output_dir: Path) -> dict:
    """Проверить вход повторно и опубликовать полный независимый набор артефактов."""
    started = time.perf_counter()
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ImportFailure("Каталог результатов не пуст: предыдущий анализ не перезаписывается.")
    dataset, config, graph, display, facts, tables = _prepare(sources, config)
    report = _preview(dataset, config, graph)
    report.update({"schema_version": SCHEMA_VERSION, "rules_version": RULES_VERSION,
                   "sources": _provenance(sources), "versions": {"python": platform.python_version(), "pandas": pd.__version__, "networkx": nx.__version__},
                   "csv_policy": {"encoding": "UTF-8 BOM", "formula_id_prefix": "'",
                                  "id_encoding": "apostrophe-prefix-v1" if config["profile"] == "generic" else "unchanged-int64",
                                  "id_prefix_triggers": ["=", "+", "-", "@", "'"],
                                  "id_prefix_scope": "Только колонки gid пользовательских CSV; начальные пробелы не скрывают триггер.",
                                  "id_decoding": "В generic CSV удалите ровно один начальный апостроф, если он есть; остальное значение не меняйте.",
                                  "canonical_ids": "result.json и graph_view.html сохраняют исходные ID без добавленного CSV-префикса.",
                                  "top_gids": "JSON array" if config["profile"] == "generic" else "comma-separated int64"},
                   "rules": {"consolidator_min_payers": CONSOLIDATOR_MIN_PAYERS,
                             "distributor_min_recipients": DISTRIBUTOR_MIN_RECIPIENTS,
                             "coordinator": {"in_min": 2, "out_min": 2, "centrality_min": COORDINATOR_CENTRALITY_PERCENTILE, "turnover_component_min": 0.75},
                             "transit_ratio": [TRANSIT_RATIO_LOW, TRANSIT_RATIO_HIGH],
                             "terminal": {"retention_min": 0.8, "out_in_max": 0.2},
                             "flow_eligibility": "coverage=outward; depth < max_depth; is_seed=false; all facts known",
                             "role_order": ["coordinator", "distributor", "consolidator", "transit", "terminal", "peripheral"],
                             "priority_weights": {"role_base": 0.45, "rule_strength": 0.25, "centrality": 0.20, "turnover_percentile": 0.10},
                             "role_base": ROLE_BASE_PRIORITY, "boundary_priority_multiplier": 0.85},
                   "amount_total": _money(sum(int(value) for value in dataset.edges["amount_minor"]), config["decimals"]),
                   "amount_total_minor": str(sum(int(value) for value in dataset.edges["amount_minor"])),
                   "limitations": ["Наблюдаемый граф не доказывает полный баланс или виновность.",
                                   "Пороговые правила фиксированы и не калиброваны по размеченным ролям.",
                                   "Исходный идентификатор не дополняется внешними атрибутами.",
                                   "Betweenness для компонент >300 узлов использует 128 опорных вершин с seed=42."]})
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="analysis-", dir=output_dir.parent) as temporary:
        staging = Path(temporary) / "result"
        staging.mkdir()
        if config["profile"] == "hackalem":
            inputs = Path(temporary) / "inputs"
            inputs.mkdir()
            for name, table in zip(("nodes", "edges", "transactions"), tables, strict=True):
                table.to_parquet(inputs / f"{name}.parquet", index=False)
            strict_report = run_pipeline(inputs, staging)
            report["hackalem"] = strict_report
            report["counts"].update(clusters=int(strict_report["n_clusters"]), top_nodes=int(strict_report["top_nodes"]))
            report["role_distribution"] = strict_report["role_distribution"]
            # Preserve canonical IDs/unknown facts in result.json for both profiles.
            public_roles = pd.read_csv(staging / "nodes_roles.csv", dtype={"gid": str})
            roles = public_roles.set_index("gid").to_dict("index")
        else:
            frame, clusters, top_nodes = _generic_outputs(dataset, config, graph, display, facts, staging)
            report["counts"].update(clusters=len(clusters), top_nodes=len(top_nodes))
            report["role_distribution"] = {str(role): int(count) for role, count in frame["role"].value_counts().items()}
            roles = {display[int(row.gid)]: {"role": row.role, "role_score": float(row.role_score),
                      "cluster_id": int(row.cluster_id), "priority_score": float(row.priority_score), "evidence": row.evidence}
                     for row in frame.itertuples(index=False)}
        report["capabilities"] = _capabilities(dataset, config, completed=True)
        report["total_seconds"] = round(time.perf_counter() - started, 4)
        result = {"schema_version": SCHEMA_VERSION, "profile": config["profile"],
                  "currency": config["currency"], "decimals": config["decimals"], "counts": report["counts"],
                  "nodes": [{"gid": str(row.gid), "depth": None if pd.isna(row.depth) else int(row.depth),
                             "is_seed": None if pd.isna(row.is_seed) else bool(row.is_seed), **roles[str(row.gid)]}
                            for row in dataset.nodes.itertuples(index=False)],
                  "edges": [{"src": str(row.src), "dst": str(row.dst), "amount_minor": str(int(row.amount_minor)),
                             "amount": _money(row.amount_minor, config["decimals"]), "n_tx": None if pd.isna(row.n_tx) else str(int(row.n_tx))}
                            for row in dataset.edges.itertuples(index=False)]}
        for name, content in (("report.json", report), ("result.json", result)):
            (staging / name).write_text(json.dumps(content, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        output_dir.mkdir(exist_ok=True)
        for name in ARTIFACTS:
            shutil.copyfile(staging / name, output_dir / name)
    return report
