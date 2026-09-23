"""Создать детерминированный синтетический набор по схеме AGENT.md.

Это генератор тестовых данных, а не попытка восстановить отсутствующий датасет.
"""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RNG = random.Random(20260923)

N_NODES = 2_248
N_EDGES = 3_119
N_TX = 4_840
TOTAL_KZT = 365_890_012

COMPONENT_SIZES = [1_877, 270, 17, 13, 11, 10, 9, 8, 7, 6, 5, 4, 3, 3, 3, 2]
COMPONENT_SEEDS = [46, 1, 5, 4, 4, 3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1]
DEPTH_ALLOCATIONS = [
    [392, 391, 676, 372],
    [60, 55, 95, 59],
    [3, 2, 4, 3],
    [2, 2, 3, 2],
    [2, 1, 2, 2],
    [2, 1, 2, 2],
    [2, 1, 2, 1],
    [1, 2, 1, 1],
    [1, 1, 2, 1],
    [1, 1, 1, 1],
    [1, 1, 1, 0],
    [1, 1, 0, 0],
    [1, 1, 0, 0],
    [1, 1, 0, 0],
    [1, 1, 0, 0],
    [1, 0, 0, 0],
]


def make_nodes() -> tuple[pd.DataFrame, list[dict[int, list[int]]]]:
    depth_pools = {
        0: iter(range(1, 82)),
        1: iter(range(82, 554)),
        2: iter(range(554, 1_016)),
        3: iter(range(1_016, 1_805)),
        4: iter(range(1_805, 2_249)),
    }
    components: list[dict[int, list[int]]] = []
    rows: list[dict[str, int | bool]] = []
    for seed_count, allocation in zip(COMPONENT_SEEDS, DEPTH_ALLOCATIONS, strict=True):
        by_depth: dict[int, list[int]] = {depth: [] for depth in range(5)}
        for _ in range(seed_count):
            gid = next(depth_pools[0])
            by_depth[0].append(gid)
            rows.append({"gid": gid, "depth": 0, "is_seed": True})
        for depth, count in enumerate(allocation, start=1):
            for _ in range(count):
                gid = next(depth_pools[depth])
                by_depth[depth].append(gid)
                rows.append({"gid": gid, "depth": depth, "is_seed": False})
        components.append(by_depth)

    nodes = pd.DataFrame(rows).sort_values("gid").reset_index(drop=True)
    assert len(nodes) == N_NODES
    assert nodes.groupby("depth").size().to_dict() == {0: 81, 1: 472, 2: 462, 3: 789, 4: 444}
    return nodes, components


def make_edge_pairs(components: list[dict[int, list[int]]]) -> list[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    no_out_seed = set(range(1, 32))

    for by_depth in components:
        active_seeds = [gid for gid in by_depth[0] if gid not in no_out_seed]
        inactive_seeds = [gid for gid in by_depth[0] if gid in no_out_seed]
        depth_one = by_depth[1]
        if not active_seeds:
            raise AssertionError("Каждой компоненте нужен seed с исходящим ребром")

        # Каждый активный seed получает исходящее ребро, а каждый depth=1 — родителя.
        for index, seed in enumerate(active_seeds):
            pairs.add((seed, depth_one[index % len(depth_one)]))
        for index, child in enumerate(depth_one):
            pairs.add((active_seeds[index % len(active_seeds)], child))

        # Горизонтальная цепочка связывает ветви одной заявленной компоненты.
        # У каждого depth=1 всё равно остаётся прямой путь от seed длины 1.
        for left, right in zip(depth_one, depth_one[1:]):
            pairs.add((left, right))

        # Сохраняем 31 seed без исходящих рёбер, но не превращаем 19 из них в
        # отдельные компоненты: они связаны только наблюдаемым входящим потоком.
        for index, seed in enumerate(inactive_seeds):
            pairs.add((depth_one[index % len(depth_one)], seed))

        for depth in (2, 3, 4):
            parents = by_depth[depth - 1]
            for index, child in enumerate(by_depth[depth]):
                pairs.add((parents[index % len(parents)], child))

    largest = components[0]
    special_fan_116 = largest[1][0]
    special_fan_60 = largest[1][1]

    def ensure_outdegree(source: int, targets: list[int], degree: int) -> None:
        for target in targets:
            if sum(1 for src, _ in pairs if src == source) >= degree:
                break
            pairs.add((source, target))

    def ensure_indegree(target: int, sources: list[int], degree: int) -> None:
        for source in sources:
            if sum(1 for _, dst in pairs if dst == target) >= degree:
                break
            pairs.add((source, target))

    ensure_outdegree(special_fan_116, largest[2], 116)
    ensure_outdegree(special_fan_60, largest[2], 60)
    special_sources = {special_fan_116, special_fan_60}
    ordinary_depth_one = [gid for gid in largest[1] if gid not in special_sources]
    ensure_indegree(largest[2][200], ordinary_depth_one, 24)
    ensure_indegree(largest[2][201], ordinary_depth_one, 12)
    ensure_indegree(largest[3][300], largest[2], 8)

    candidates: list[tuple[int, int]] = []
    for by_depth in components:
        for depth in (0, 1, 2, 3):
            sources = [gid for gid in by_depth[depth] if gid not in no_out_seed and gid not in special_sources]
            targets = by_depth[depth + 1]
            candidates.extend((source, target) for source in sources for target in targets)
    RNG.shuffle(candidates)
    for pair in candidates:
        if len(pairs) >= N_EDGES:
            break
        pairs.add(pair)
    if len(pairs) != N_EDGES:
        raise AssertionError(f"Не удалось получить {N_EDGES} уникальных рёбер: {len(pairs)}")
    return sorted(pairs)


def make_edges_and_transactions(nodes: pd.DataFrame, pairs: list[tuple[int, int]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    depth_by_gid = nodes.set_index("gid")["depth"].to_dict()
    tx_counts = {pair: 1 + (1 if index < N_TX - N_EDGES else 0) for index, pair in enumerate(pairs)}
    minimums = {pair: count * 5_000 for pair, count in tx_counts.items()}
    amounts = {
        pair: minimums[pair] + 15_000 + ((pair[0] * 31 + pair[1] * 17) % 20_000)
        for pair in pairs
    }

    incoming: dict[int, list[tuple[int, int]]] = defaultdict(list)
    outgoing: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for pair in pairs:
        outgoing[pair[0]].append(pair)
        incoming[pair[1]].append(pair)

    # Ровно 72 заранее выбранных узла получают пропуск 1.0. Выбираются
    # по структуре, а не по gid: внутренний уровень, один исходящий маршрут.
    transit_candidates = [
        gid
        for gid in nodes.loc[nodes["depth"].isin([1, 2, 3]), "gid"]
        if incoming[gid] and len(outgoing[gid]) == 1
    ][:72]
    if len(transit_candidates) < 72:
        raise AssertionError("Недостаточно структурных кандидатов для транзита")
    transit_set = set(transit_candidates)

    def set_outgoing_total(keys: list[tuple[int, int]], desired: int) -> None:
        required = sum(minimums[key] for key in keys)
        desired = max(desired, required)
        remainder = desired - required
        quotient, extra = divmod(remainder, len(keys))
        for index, key in enumerate(keys):
            amounts[key] = minimums[key] + quotient + (1 if index < extra else 0)

    # Обрабатываем источники в топологическом порядке. Остальные внутренние узлы
    # намеренно получают коэффициент 0.55 или 1.55, чтобы контрольные 72
    # транзитных профиля не возникли случайно из почти одинаковых сумм.
    ordered_internal = sorted(
        (gid for gid in depth_by_gid if depth_by_gid[gid] in (1, 2, 3)),
        key=lambda value: (depth_by_gid[value], value),
    )
    for gid in ordered_internal:
        if not incoming[gid] or not outgoing[gid]:
            continue
        received = sum(amounts[key] for key in incoming[gid])
        if gid in transit_set:
            set_outgoing_total(outgoing[gid], received)
        else:
            factor = 0.55 if gid % 2 == 0 else 1.55
            desired = round(received * factor)
            if desired < sum(minimums[key] for key in outgoing[gid]):
                desired = round(received * 1.55)
            set_outgoing_total(outgoing[gid], desired)

    # Добираем контрольный оборот одним ребром depth=3 -> depth=4. Получатель
    # находится на границе и не имеет исходящих рёбер; источник не относится к
    # 72 транзитным профилям, поэтому поправка не создаёт ложный transit.
    current_total = sum(amounts.values())
    delta = TOTAL_KZT - current_total
    if delta < 0:
        raise AssertionError(f"Базовая генерация превысила контрольный оборот на {-delta}")
    rebalance_edge = next(
        pair
        for pair in pairs
        if depth_by_gid[pair[0]] == 3 and depth_by_gid[pair[1]] == 4 and pair[0] not in transit_set
    )
    amounts[rebalance_edge] += delta
    assert sum(amounts.values()) == TOTAL_KZT

    edge_rows: list[dict[str, int]] = []
    tx_rows: list[dict[str, int | pd.Timestamp]] = []
    for edge_index, (src, dst) in enumerate(pairs):
        n_tx = tx_counts[(src, dst)]
        amount = amounts[(src, dst)]
        edge_rows.append(
            {"src": src, "dst": dst, "sum_kzt": amount, "n_tx": n_tx, "depth": int(depth_by_gid[dst])}
        )
        base, remainder = divmod(amount, n_tx)
        for tx_index in range(n_tx):
            tx_rows.append(
                {
                    "src": src,
                    "dst": dst,
                    "date": pd.Timestamp(2026, 7, 1 + ((edge_index + tx_index * 7) % 31)),
                    "sum_kzt": base + (1 if tx_index < remainder else 0),
                }
            )

    edges = pd.DataFrame(edge_rows).astype({column: "int64" for column in ("src", "dst", "sum_kzt", "n_tx", "depth")})
    transactions = pd.DataFrame(tx_rows)
    transactions[["src", "dst", "sum_kzt"]] = transactions[["src", "dst", "sum_kzt"]].astype("int64")
    assert len(edges) == N_EDGES
    assert len(transactions) == N_TX
    assert int(transactions["sum_kzt"].sum()) == TOTAL_KZT
    return edges, transactions


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    nodes, components = make_nodes()
    pairs = make_edge_pairs(components)
    edges, transactions = make_edges_and_transactions(nodes, pairs)
    nodes.to_parquet(DATA_DIR / "nodes.parquet", index=False)
    edges.to_parquet(DATA_DIR / "edges.parquet", index=False)
    transactions.to_parquet(DATA_DIR / "transactions.parquet", index=False)
    print(
        f"Созданы nodes={len(nodes)}, edges={len(edges)}, transactions={len(transactions)}, "
        f"sum_kzt={int(edges['sum_kzt'].sum())}"
    )


if __name__ == "__main__":
    main()
