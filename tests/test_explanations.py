"""Регрессии смысла процентилей и точности объяснений, без изменения скоринга."""
from __future__ import annotations

import unittest

import pandas as pd

from aml_graph.clustering import detect_clusters
from aml_graph.config import DATA_DIR, ROLE_BASE_PRIORITY
from aml_graph.exports import build_top_nodes
from aml_graph.metrics import calculate_metrics
from aml_graph.roles import _assign_one, assign_roles, calculate_priority
from starter.data_loader import load_data
from starter.export_templates import TOP_NODES_COLUMNS
from starter.graph_builder import build_graph


class ExplanationUnitTests(unittest.TestCase):
    def row(self, **overrides) -> dict:
        result = {
            "gid": 11, "role": "coordinator", "role_score": 0.812345678,
            "priority_score": 0.9520129, "cluster_id": 0,
            "betweenness_component_pct": 1.0, "pagerank_component_pct": 0.95,
            "turnover_global_pct": 0.991234567, "turnover_component_pct": 0.90,
            "depth": 2, "is_seed": False, "in_degree": 3, "out_degree": 4,
            "sum_in": 100_000, "sum_out": 110_000,
            "pass_through_ratio": 1.1, "retention_ratio": 0.0,
        }
        result.update(overrides)
        return result

    def test_why_describes_average_rank_percentiles_not_strictly_smaller_nodes(self):
        why = build_top_nodes(pd.DataFrame([self.row()])).iloc[0]["why"]
        self.assertNotRegex(why, r"выше\s+\d+%\s+узлов")
        self.assertIn("Процентили", why)
        self.assertIn("0–100", why)
        self.assertIn("100×средний ранг/N", why)
        self.assertIn("равным значениям", why)
        self.assertIn("100.000000", why)
        self.assertIn("99.123457", why)

    def test_why_uses_supplied_priority_instead_of_recomputing_formula(self):
        # Это уже готовый итог; пересчёт по остальным столбцам дал бы иной скор.
        source = pd.DataFrame([self.row(priority_score=0.123456789)])
        result = build_top_nodes(source)
        self.assertIn(f"{result.iloc[0]['priority_score']:.6f}", result.iloc[0]["why"])
        self.assertIn(f"вес {ROLE_BASE_PRIORITY['coordinator']:.2f}", result.iloc[0]["why"])
        self.assertIn("сила правила 0.812346", result.iloc[0]["why"])
        self.assertIn("не вероятность", result.iloc[0]["why"])
        self.assertEqual(source.iloc[0]["priority_score"], 0.123456789)

    def test_why_shows_the_actual_boundary_adjusted_priority(self):
        frame = pd.DataFrame([self.row(role="peripheral", role_score=0.30, depth=4, out_degree=0)])
        adjusted = calculate_priority(frame)
        unadjusted = calculate_priority(frame, boundary_depth=None)
        self.assertLess(adjusted.iloc[0]["priority_score"], unadjusted.iloc[0]["priority_score"])
        result = build_top_nodes(adjusted)
        self.assertIn(f"{result.iloc[0]['priority_score']:.6f}", result.iloc[0]["why"])
        self.assertIn("поправками", result.iloc[0]["why"])

    def test_true_ties_keep_equal_explanations_and_existing_gid_tiebreak(self):
        result = build_top_nodes(pd.DataFrame([self.row(gid=22), self.row(gid=11)]))
        self.assertEqual(result["gid"].tolist(), [11, 22])
        self.assertEqual(result["why"].nunique(), 1)
        self.assertNotIn("gid", result.iloc[0]["why"].lower())
        self.assertNotIn("rank", result.iloc[0]["why"].lower())
        self.assertEqual(list(result.columns), TOP_NODES_COLUMNS)

    def test_sort_uses_unrounded_scores_and_does_not_mutate_source(self):
        frame = pd.DataFrame([
            self.row(gid=11, priority_score=0.5, role_score=0.9),
            self.row(gid=22, priority_score=0.5000001, role_score=0.1),
            self.row(gid=33, priority_score=0.5, role_score=0.8),
        ])
        before = frame.copy(deep=True)
        result = build_top_nodes(frame)
        self.assertEqual(result["gid"].tolist(), [22, 11, 33])
        self.assertEqual(result["priority_score"].tolist(), [0.5, 0.5, 0.5])
        pd.testing.assert_frame_equal(frame, before)

    def test_coordinator_evidence_states_percentile_and_scale_under_200_chars(self):
        role, score, evidence = _assign_one(pd.Series(self.row()))
        self.assertEqual(role, "coordinator")
        self.assertGreater(score, 0)
        self.assertNotRegex(evidence, r"выше\s+\d+%\s+узлов")
        self.assertIn("процентиль", evidence)
        self.assertIn("100.00", evidence)
        self.assertIn("0–100", evidence)
        self.assertLessEqual(len(evidence), 200)


class CurrentRankingExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nodes, edges, _ = load_data(DATA_DIR)
        graph = build_graph(nodes, edges)
        frame = assign_roles(calculate_metrics(graph))
        frame["cluster_id"] = frame["gid"].map(detect_clusters(graph)).astype(int)
        cls.frame = calculate_priority(frame)
        cls.top = build_top_nodes(cls.frame)

    def test_current_third_and_fourth_positions_explain_real_score_difference(self):
        third, fourth = self.top.iloc[2], self.top.iloc[3]
        self.assertGreater(third["priority_score"], fourth["priority_score"])
        self.assertNotEqual(third["why"], fourth["why"])
        for row in (third, fourth):
            self.assertIn(f"{row['priority_score']:.6f}", row["why"])

    def test_all_current_evidence_keeps_length_and_avoids_false_percentage_claim(self):
        self.assertTrue(self.frame["evidence"].str.len().le(200).all())
        self.assertFalse(self.frame["evidence"].str.contains(r"выше\s+\d+%\s+узлов", regex=True).any())
        self.assertFalse(self.top["why"].str.contains(r"выше\s+\d+%\s+узлов", regex=True).any())


if __name__ == "__main__":
    unittest.main()
