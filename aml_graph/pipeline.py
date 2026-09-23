"""Оркестрация полного запуска и печать коротких отчётов по шагам."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from starter.data_loader import load_data
from starter.graph_builder import build_graph

from .clustering import build_clusters_table, detect_clusters
from .config import DATA_DIR, OUTPUT_DIR, EXPECTED_DEPTH_COUNTS, EXPECTED_TOTAL_KZT
from .exports import build_nodes_roles, build_top_nodes, validate_outputs, write_outputs
from .metrics import calculate_metrics, candidate_summary
from .roles import assign_roles, calculate_priority
from .validation import validate_data
from .visualization import demo_source_matches, write_graph_view


def run_pipeline(data_dir: Path = DATA_DIR, output_dir: Path = OUTPUT_DIR) -> dict[str, object]:
    started = time.perf_counter()
    stage_times: dict[str, float] = {}

    marker = time.perf_counter()
    nodes, edges, transactions = load_data(data_dir)
    graph = build_graph(nodes, edges)
    validation = validate_data(nodes, edges, transactions, graph)
    stage_times["load_validate_graph"] = time.perf_counter() - marker
    validation["expected_depth_counts"] = EXPECTED_DEPTH_COUNTS
    validation["expected_small_component_range"] = [2, 17]
    validation["expected_total_kzt"] = EXPECTED_TOTAL_KZT
    validation["demo_control_match"] = {
        "depth_counts": validation["depth_counts"] == EXPECTED_DEPTH_COUNTS,
        "component_sizes": (
            len(validation["component_sizes"]) == 16
            and validation["component_sizes"][:2] == [1_877, 270]
            and all(2 <= size <= 17 for size in validation["component_sizes"][2:])
        ),
        "sum_kzt": validation["sum_kzt"] == EXPECTED_TOTAL_KZT,
    }
    print(f"[2] Граф: {validation}")

    marker = time.perf_counter()
    metrics = calculate_metrics(graph)
    candidates = candidate_summary(metrics)
    if candidates["collectors_8_24"] == 0 or candidates["fans_60_116"] == 0:
        raise AssertionError("Не найдены ожидаемые кандидаты: проверьте расчёт степеней")
    if candidates["pass_through_08_12"] < 72:
        raise AssertionError("Найдено менее 72 ожидаемых pass-through профилей")
    stage_times["metrics"] = time.perf_counter() - marker
    print(f"[3] Кандидаты: {candidates}")

    marker = time.perf_counter()
    frame = assign_roles(metrics)
    role_distribution = frame["role"].value_counts().sort_index().astype(int).to_dict()
    missing_roles = {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"} - set(role_distribution)
    if missing_roles:
        print(f"[4] Предупреждение: в фактических данных нет узлов с признаками ролей {sorted(missing_roles)}")
    stage_times["roles"] = time.perf_counter() - marker
    print(f"[4] Роли: {role_distribution}")

    marker = time.perf_counter()
    cluster_by_gid = detect_clusters(graph)
    frame["cluster_id"] = frame["gid"].map(cluster_by_gid).astype(int)
    frame = calculate_priority(frame)
    clusters = build_clusters_table(graph, frame)
    multi_seed_clusters = int((clusters["n_seed"] > 1).sum())
    if multi_seed_clusters < 8:
        raise AssertionError(f"Ожидалось ≥8 кластеров с несколькими seed, получено {multi_seed_clusters}")
    stage_times["clustering_priority"] = time.perf_counter() - marker
    print(f"[5–6] Кластеры: {len(clusters)}, с >1 seed: {multi_seed_clusters}")

    marker = time.perf_counter()
    nodes_roles = build_nodes_roles(frame)
    top_nodes = build_top_nodes(frame)
    validate_outputs(nodes_roles, clusters, top_nodes)
    # Finish rendering and serializing the whole set before replacing any
    # previous result. A missing UI asset or encoding/render error must not
    # leave new CSVs beside an old graph and report.
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pipeline-", dir=output_dir.parent) as temporary:
        staging = Path(temporary)
        write_outputs(staging, nodes_roles, clusters, top_nodes)
        write_graph_view(
            staging / "graph_view.html", graph, frame,
            clusters=clusters, top_nodes=top_nodes,
            is_demo=demo_source_matches(data_dir),
            period=f"{transactions['date'].min():%d.%m.%Y} — {transactions['date'].max():%d.%m.%Y}",
        )
        stage_times["exports_visualization"] = time.perf_counter() - marker

        elapsed = time.perf_counter() - started
        report = {
            "input": validation,
            "candidates": candidates,
            "role_distribution": role_distribution,
            "n_clusters": int(len(clusters)),
            "multi_seed_clusters": multi_seed_clusters,
            "top_nodes": int(len(top_nodes)),
            "stage_seconds": {key: round(value, 4) for key, value in stage_times.items()},
            "total_seconds": round(elapsed, 4),
        }
        (staging / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "graph_view.html", "run_report.json"):
            # Create files under the destination ACL instead of moving files
            # out of an owner-only Windows temporary directory.
            shutil.copyfile(staging / name, output_dir / name)
    print(f"[7–9] Выгрузки и экран готовы за {elapsed:.2f} с: {output_dir}")
    return report
