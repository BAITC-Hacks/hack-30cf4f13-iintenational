"""Явные правила ролей и формула приоритета без ML/чёрного ящика."""

from __future__ import annotations

import math

import pandas as pd

from .config import (
    CONSOLIDATOR_MIN_PAYERS,
    COORDINATOR_CENTRALITY_PERCENTILE,
    DISTRIBUTOR_MIN_RECIPIENTS,
    ROLE_BASE_PRIORITY,
    TRANSIT_RATIO_HIGH,
    TRANSIT_RATIO_LOW,
)


ROLES = {"consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"}


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _format_amount(value: int, decimals: int) -> str:
    if decimals == 0:
        return f"{value:,}"
    whole, fraction = divmod(value, 10 ** decimals)
    return f"{whole:,}.{fraction:0{decimals}d}"


def _assign_one(
    row: pd.Series, *, coverage: str = "outward", max_depth: int | None = 4,
    currency: str = "KZT", decimals: int = 0,
) -> tuple[str, float, str]:
    centrality_pct = max(float(row["betweenness_component_pct"]), float(row["pagerank_component_pct"]))
    depth = None if pd.isna(row["depth"]) else int(row["depth"])
    seed = None if pd.isna(row["is_seed"]) else bool(row["is_seed"])
    boundary = coverage == "outward" and max_depth is not None and depth == max_depth
    flow_observed = (
        coverage == "outward" and max_depth is not None
        and depth is not None and depth < max_depth and seed is False
    )
    in_degree = int(row["in_degree"])
    out_degree = int(row["out_degree"])
    ratio = float(row["pass_through_ratio"]) if not pd.isna(row["pass_through_ratio"]) else math.nan

    if (
        in_degree >= 2
        and out_degree >= 2
        and centrality_pct >= COORDINATOR_CENTRALITY_PERCENTILE
        and float(row["turnover_component_pct"]) >= 0.75
    ):
        degree_signal = _clip((in_degree + out_degree) / 20)
        score = 0.50 * centrality_pct + 0.25 * degree_signal + 0.25 * float(row["turnover_component_pct"])
        evidence = (
            f"Гипотеза координации: in={in_degree}, out={out_degree}; "
            f"процентиль центральности компоненты {centrality_pct * 100:.2f} "
            f"(шкала 0–100, порог ≥{COORDINATOR_CENTRALITY_PERCENTILE * 100:g})."
        )
        return "coordinator", _clip(score), evidence

    if out_degree >= DISTRIBUTOR_MIN_RECIPIENTS:
        strength = _clip((out_degree - DISTRIBUTOR_MIN_RECIPIENTS) / (60 - DISTRIBUTOR_MIN_RECIPIENTS))
        score = 0.55 + 0.45 * strength
        evidence = (
            f"Признаки распределения: {out_degree} уникальных получателей "
            f"(порог ≥{DISTRIBUTOR_MIN_RECIPIENTS}), исходящий поток {_format_amount(int(row['sum_out']), decimals)} {currency}."
        )
        return "distributor", _clip(score), evidence

    if in_degree >= CONSOLIDATOR_MIN_PAYERS:
        strength = _clip((in_degree - CONSOLIDATOR_MIN_PAYERS) / (24 - CONSOLIDATOR_MIN_PAYERS))
        score = 0.55 + 0.45 * strength
        evidence = (
            f"Признаки консолидации: {in_degree} уникальных плательщиков "
            f"(порог ≥{CONSOLIDATOR_MIN_PAYERS}), входящий поток {_format_amount(int(row['sum_in']), decimals)} {currency}."
        )
        return "consolidator", _clip(score), evidence

    if (
        flow_observed
        and in_degree >= 1
        and out_degree >= 1
        and TRANSIT_RATIO_LOW <= ratio <= TRANSIT_RATIO_HIGH
    ):
        proximity = 1.0 - abs(ratio - 1.0) / 0.2
        score = 0.60 + 0.40 * _clip(proximity)
        evidence = (
            f"Признаки транзита: out/in={ratio:.2f} в диапазоне "
            f"{TRANSIT_RATIO_LOW:.1f}–{TRANSIT_RATIO_HIGH:.1f}; in={in_degree}, out={out_degree}."
        )
        return "transit", _clip(score), evidence

    # Объявленная граница исключена: отсутствие исходящих вызвано обходом.
    # В объявленном исходящем обходе seed исключён: его вход системно неполон.
    if (
        flow_observed
        and in_degree >= 1
        and float(row["retention_ratio"]) >= 0.8
        and (out_degree == 0 or ratio <= 0.2)
    ):
        score = 0.65 + 0.25 * _clip(float(row["retention_ratio"])) + 0.10 * float(row["turnover_component_pct"])
        evidence = (
            f"Гипотеза терминального поведения: depth={depth}, in={in_degree}, out={out_degree}, "
            f"удержание наблюдаемого входа {float(row['retention_ratio']):.0%}; узел не на границе depth={max_depth}."
        )
        return "terminal", _clip(score), evidence

    if seed is True and in_degree > 0 and out_degree == 0:
        return (
            "peripheral",
            0.32,
            ("Периферия: seed получает наблюдаемый вход, но его внешний входящий поток неполон; терминальность не оценивается."
             if coverage == "outward" else
             "Периферия: seed получает наблюдаемый вход; полнота внешних потоков неизвестна, терминальность не оценивается."),
        )

    if boundary and out_degree == 0:
        return (
            "peripheral",
            0.30,
            f"Периферия: depth={max_depth} — граница выгрузки; out=0 не трактуется как оседание средств.",
        )

    if seed is True and out_degree == 0:
        return (
            "peripheral",
            0.32,
            ("Периферия: seed без наблюдаемых исходящих; неполный входящий поток не позволяет вывод о балансе."
             if coverage == "outward" else
             "Периферия: seed без наблюдаемых исходящих; полнота внешних потоков неизвестна, вывод о балансе недоступен."),
        )

    if not flow_observed and (coverage != "outward" or depth is None or seed is None):
        return (
            "peripheral", 0.35,
            f"Периферия: структурные пороги не достигнуты; in={in_degree}, out={out_degree}. "
            "Полнота наблюдений неизвестна; транзит и терминальность не оцениваются.",
        )

    return (
        "peripheral",
        0.35,
        f"Периферия: пороги специальных ролей не достигнуты; in={in_degree}, out={out_degree}, depth={depth}.",
    )


def assign_roles(
    metrics: pd.DataFrame, *, coverage: str = "outward", max_depth: int | None = 4,
    currency: str = "KZT", decimals: int = 0,
) -> pd.DataFrame:
    result = metrics.copy()
    assigned = result.apply(
        lambda row: _assign_one(row, coverage=coverage, max_depth=max_depth, currency=currency, decimals=decimals),
        axis=1, result_type="expand",
    )
    assigned.columns = ["role", "role_score", "evidence"]
    result = pd.concat([result, assigned], axis=1)
    result["role_score"] = result["role_score"].astype(float).clip(0, 1)
    if not set(result["role"]).issubset(ROLES):
        raise AssertionError("Назначена роль вне обязательного словаря")
    if (result["evidence"].str.len() > 200).any():
        raise AssertionError("evidence превышает 200 символов")
    return result


def calculate_priority(frame: pd.DataFrame, *, boundary_depth: int | None = 4) -> pd.DataFrame:
    result = frame.copy()
    centrality = result[["betweenness_component_pct", "pagerank_component_pct"]].max(axis=1)
    role_base = result["role"].map(ROLE_BASE_PRIORITY).astype(float)
    score = (
        0.45 * role_base
        + 0.25 * result["role_score"].astype(float)
        + 0.20 * centrality
        + 0.10 * result["turnover_global_pct"].astype(float)
    )
    # Объявленная граница не должна подниматься в топ только из-за
    # искусственного нулевого out-degree.
    boundary = (
        (result["depth"] == boundary_depth) & (result["out_degree"] == 0)
        if boundary_depth is not None else pd.Series(False, index=result.index)
    ).fillna(False)
    score.loc[boundary] *= 0.85
    result["priority_score"] = score.clip(0, 1)
    return result
