"""Контракт входа, смысл метрик и независимые проверки итоговых выгрузок."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import networkx as nx
import pandas as pd

from aml_graph.clustering import detect_clusters
from aml_graph.config import DATA_DIR
from aml_graph.metrics import calculate_metrics
from aml_graph.pipeline import run_pipeline
from aml_graph.roles import ROLES, _assign_one
from aml_graph.validation import DataValidationError, validate_data
from starter.data_loader import load_data
from starter.graph_builder import build_graph


class InputValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.valid = load_data(DATA_DIR)

    def tables(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        return tuple(frame.copy(deep=True) for frame in self.valid)

    def validate(self, tables: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]) -> dict:
        nodes, edges, transactions = tables
        return validate_data(nodes, edges, transactions, build_graph(nodes, edges))

    def overflow_tables(self, *, transactions_only: bool = False) -> tuple:
        """Keep every value in int64 while the true total increases by 2**64."""
        nodes, edges, transactions = self.tables()
        if transactions_only:
            # Put four transactions on one pair, preserving every existing pair,
            # row count and the original exact total before injecting overflow.
            pairs = list(edges.loc[edges["n_tx"].eq(2)].head(3).itertuples())
            target = pairs[0]
            for source in pairs[1:]:
                index = transactions.index[
                    transactions["src"].eq(source.src) & transactions["dst"].eq(source.dst)
                ][0]
                transactions.loc[index, ["src", "dst"]] = [target.src, target.dst]
            grouped = transactions.groupby(["src", "dst"], as_index=False).agg(
                sum_kzt=("sum_kzt", "sum"), n_tx=("sum_kzt", "size")
            )
            edges = edges.drop(columns=["sum_kzt", "n_tx"]).merge(
                grouped, on=["src", "dst"], validate="one_to_one"
            )[self.valid[1].columns]
            self.validate((nodes, edges, transactions))
            affected = transactions.index[
                transactions["src"].eq(target.src) & transactions["dst"].eq(target.dst)
            ]
            self.assertEqual(len(affected), 4)
            for index in affected:
                transactions.at[index, "sum_kzt"] = int(transactions.at[index, "sum_kzt"]) + 2**62
        else:
            for index, row in edges.loc[edges["n_tx"].eq(1)].head(4).iterrows():
                edges.at[index, "sum_kzt"] = int(row.sum_kzt) + 2**62
                affected = transactions.index[
                    transactions["src"].eq(row.src) & transactions["dst"].eq(row.dst)
                ][0]
                transactions.at[affected, "sum_kzt"] = int(transactions.at[affected, "sum_kzt"]) + 2**62
        return nodes, edges, transactions

    def wrong_component_seed_tables(self) -> tuple:
        nodes, edges, transactions = self.tables()
        components = sorted(nx.weakly_connected_components(build_graph(nodes, edges)), key=len, reverse=True)
        old_seed = nodes.index[nodes["gid"].isin(components[0]) & nodes["is_seed"]][0]
        new_seed = nodes.index[nodes["gid"].isin(components[1]) & nodes["depth"].eq(1)][0]
        nodes.loc[[old_seed, new_seed], "is_seed"] = [False, True]
        nodes.loc[[old_seed, new_seed], "depth"] = [1, 0]
        edges["depth"] = edges["dst"].map(nodes.set_index("gid")["depth"])
        return nodes, edges, transactions

    def assert_loader_rejects(self, tables: tuple, message: str) -> None:
        with patch("starter.data_loader.pd.read_parquet", side_effect=tables):
            with self.assertRaisesRegex(DataValidationError, message):
                load_data(Path("unused-fixture"))

    def test_missing_column_fails_before_graph_construction(self) -> None:
        nodes, edges, transactions = self.tables()
        self.assert_loader_rejects((nodes.drop(columns="gid"), edges, transactions), "отсутствуют колонки")

    def test_fractional_identifiers_are_not_silently_truncated(self) -> None:
        nodes, edges, transactions = self.tables()
        nodes["gid"] = nodes["gid"].astype(float)
        nodes.loc[0, "gid"] += 0.5
        self.assert_loader_rejects((nodes, edges, transactions), "целочисленный тип")

    def test_string_false_is_not_converted_to_true(self) -> None:
        nodes, edges, transactions = self.tables()
        nodes["is_seed"] = nodes["is_seed"].astype(str)
        self.assert_loader_rejects((nodes, edges, transactions), "ожидается bool")

    def test_unsigned_identifier_overflow_is_rejected(self) -> None:
        nodes, edges, transactions = self.tables()
        nodes["gid"] = nodes["gid"].astype("uint64")
        nodes.loc[0, "gid"] = 2**63
        self.assert_loader_rejects((nodes, edges, transactions), "вне диапазона int64")

    def test_missing_values_are_rejected_before_coercion(self) -> None:
        nodes, edges, transactions = self.tables()
        nodes["is_seed"] = nodes["is_seed"].astype("boolean")
        nodes.loc[0, "is_seed"] = pd.NA
        self.assert_loader_rejects((nodes, edges, transactions), "пустые обязательные")

    def test_date_contract_rejects_strings_and_timezones(self) -> None:
        for replacement in (self.valid[2]["date"].astype(str), self.valid[2]["date"].dt.tz_localize("UTC")):
            with self.subTest(dtype=str(replacement.dtype)):
                nodes, edges, transactions = self.tables()
                transactions["date"] = replacement
                self.assert_loader_rejects((nodes, edges, transactions), "transactions.date")

    def test_duplicate_nodes_are_rejected(self) -> None:
        nodes, edges, transactions = self.tables()
        nodes.loc[1, "gid"] = nodes.loc[0, "gid"]
        with self.assertRaisesRegex(DataValidationError, "дубли"):
            self.validate((nodes, edges, transactions))

    def test_unknown_edge_endpoint_is_rejected(self) -> None:
        nodes, edges, transactions = self.tables()
        edges.loc[0, "dst"] = int(nodes["gid"].max()) + 1
        with self.assertRaisesRegex(DataValidationError, "неизвестные gid"):
            self.validate((nodes, edges, transactions))

    def test_duplicate_edges_are_rejected(self) -> None:
        nodes, edges, transactions = self.tables()
        edges.iloc[1] = edges.iloc[0]
        with self.assertRaisesRegex(DataValidationError, "повторные пары"):
            self.validate((nodes, edges, transactions))

    def test_seed_flag_must_match_depth_zero(self) -> None:
        nodes, edges, transactions = self.tables()
        seed_index = nodes.index[nodes["is_seed"]][0]
        regular_index = nodes.index[~nodes["is_seed"]][0]
        nodes.loc[seed_index, "is_seed"] = False
        nodes.loc[regular_index, "is_seed"] = True
        with self.assertRaisesRegex(DataValidationError, "is_seed должен совпадать"):
            self.validate((nodes, edges, transactions))

    def test_last_instant_of_july_is_accepted(self) -> None:
        tables = self.tables()
        tables[2].loc[0, "date"] = pd.Timestamp("2026-07-31 23:59:59.999999999")
        self.assertEqual(self.validate(tables)["n_transactions"], 4_840)

    def test_dates_outside_july_are_rejected(self) -> None:
        for date in ("2026-06-30 23:59:59", "2026-08-01"):
            with self.subTest(date=date):
                tables = self.tables()
                tables[2].loc[0, "date"] = pd.Timestamp(date)
                with self.assertRaisesRegex(DataValidationError, "вне июля"):
                    self.validate(tables)

    def test_transaction_below_visibility_threshold_is_rejected(self) -> None:
        tables = self.tables()
        tables[2].loc[0, "sum_kzt"] = 4_999
        with self.assertRaisesRegex(DataValidationError, "ниже порога"):
            self.validate(tables)

    def test_wrong_edge_aggregation_is_rejected_even_with_same_total(self) -> None:
        tables = self.tables()
        tables[1].loc[0, "sum_kzt"] += 1
        tables[1].loc[1, "sum_kzt"] -= 1
        with self.assertRaisesRegex(DataValidationError, "агрегации transactions"):
            self.validate(tables)

    def test_valid_totals_and_component_facts_are_reported(self) -> None:
        nodes, edges, transactions = self.tables()
        report = self.validate((nodes, edges, transactions))
        self.assertEqual(report["sum_kzt"], sum(map(int, edges["sum_kzt"])))
        self.assertEqual(report["sum_kzt"], sum(map(int, transactions["sum_kzt"])))
        self.assertEqual(report["component_seed_counts"][:2], [46, 1])
        self.assertEqual(sum(report["component_seed_counts"]), 81)
        self.assertEqual(len(report["component_seed_counts"]), len(report["component_sizes"]))
        self.assertEqual(report["outside_largest_component"], len(nodes) - report["component_sizes"][0])
        self.assertEqual(report["nodes_out_gt_in"], 869)

    def test_int64_overflow_in_both_money_tables_is_rejected(self) -> None:
        tables = self.overflow_tables()
        self.assertEqual(sum(map(int, tables[1]["sum_kzt"])), 2**64 + 365_890_012)
        self.assertEqual(int(tables[1]["sum_kzt"].sum()), 365_890_012)
        self.assertEqual(int(tables[2]["sum_kzt"].sum()), 365_890_012)
        with patch.object(tables[2], "groupby", side_effect=AssertionError("Exact totals must be checked before groupby")):
            with self.assertRaisesRegex(DataValidationError, "Оборот edges"):
                self.validate(tables)

    def test_transaction_only_overflow_is_rejected_before_groupby(self) -> None:
        tables = self.overflow_tables(transactions_only=True)
        edges, transactions = tables[1:]
        self.assertEqual(sum(map(int, transactions["sum_kzt"])), 2**64 + 365_890_012)
        self.assertEqual(sum(map(int, edges["sum_kzt"])), 365_890_012)
        # A wrapped groupby falsely agrees with the still-valid edge table.
        wrapped = transactions.groupby(["src", "dst"])["sum_kzt"].sum().sort_index()
        pd.testing.assert_series_equal(wrapped, edges.set_index(["src", "dst"])["sum_kzt"].sort_index())
        with patch.object(transactions, "groupby", side_effect=AssertionError("Exact totals must be checked before groupby")):
            with self.assertRaisesRegex(DataValidationError, "Оборот transactions"):
                self.validate(tables)

    def test_largest_component_seed_controls_cannot_be_swapped(self) -> None:
        tables = self.wrong_component_seed_tables()
        self.assertEqual(int(tables[0]["is_seed"].sum()), 81)
        self.assertEqual(tables[0].groupby("depth").size().to_dict(), {0: 81, 1: 472, 2: 462, 3: 789, 4: 444})
        with self.assertRaisesRegex(DataValidationError, "seed.*крупнейших компонент"):
            self.validate(tables)

    def test_invalid_overflow_or_component_seeds_stop_before_analysis_and_outputs(self) -> None:
        for label, tables in (
            ("edge and transaction overflow", self.overflow_tables()),
            ("transaction-only overflow", self.overflow_tables(transactions_only=True)),
            ("component seed counts", self.wrong_component_seed_tables()),
        ):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "result"
                with patch("aml_graph.pipeline.load_data", return_value=tables):
                    with patch("aml_graph.pipeline.calculate_metrics", side_effect=AssertionError("Validation must stop before analysis")) as metrics:
                        with self.assertRaises(DataValidationError):
                            run_pipeline(DATA_DIR, output)
                        metrics.assert_not_called()
                self.assertFalse(output.exists())

    def test_failed_run_does_not_publish_partial_outputs(self) -> None:
        tables = self.tables()
        tables[2].loc[0, "sum_kzt"] = 4_999
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            with patch("aml_graph.pipeline.load_data", return_value=tables):
                with self.assertRaises(DataValidationError):
                    run_pipeline(DATA_DIR, output)
            self.assertFalse(output.exists())

    def test_view_build_failure_keeps_previous_complete_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            output.mkdir()
            names = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "graph_view.html", "run_report.json")
            previous = {name: f"previous {name}".encode() for name in names}
            for name, value in previous.items():
                (output / name).write_bytes(value)
            with patch("aml_graph.pipeline.write_graph_view", side_effect=OSError("view build failed")):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, "view build failed"):
                    run_pipeline(DATA_DIR, output)
            for name in names:
                self.assertTrue((output / name).read_bytes() == previous[name], f"Previous {name} was changed")

    def test_view_build_failure_does_not_publish_new_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            with patch("aml_graph.pipeline.write_graph_view", side_effect=OSError("view build failed")):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(OSError, "view build failed"):
                    run_pipeline(DATA_DIR, output)
            self.assertFalse(output.exists())


class MetricAndRoleTests(unittest.TestCase):
    def row(self, **overrides: object) -> pd.Series:
        fields = {
            "gid": 99,
            "depth": 2,
            "is_seed": False,
            "in_degree": 1,
            "out_degree": 1,
            "sum_in": 10_000,
            "sum_out": 10_000,
            "pass_through_ratio": 1.0,
            "retention_ratio": 0.0,
            "betweenness_component_pct": 0.5,
            "pagerank_component_pct": 0.5,
            "turnover_component_pct": 0.5,
        }
        fields.update(overrides)
        return pd.Series(fields)

    def test_seed_incomplete_income_never_uses_transit_or_terminal_rules(self) -> None:
        for ratio in (0.0, 0.8, 1.0, 1.2, 5.0):
            with self.subTest(ratio=ratio):
                role, score, _ = _assign_one(self.row(is_seed=True, depth=0, pass_through_ratio=ratio, retention_ratio=1-ratio))
                self.assertNotIn(role, {"transit", "terminal"})
                self.assertGreaterEqual(score, 0)

    def test_depth_four_zero_output_is_not_a_terminal(self) -> None:
        role, _, evidence = _assign_one(self.row(depth=4, out_degree=0, sum_out=0, pass_through_ratio=0.0, retention_ratio=1.0))
        self.assertEqual(role, "peripheral")
        self.assertIn("граница", evidence)

    def test_observed_internal_terminal_has_explicit_retention_rule(self) -> None:
        role, _, _ = _assign_one(self.row(out_degree=0, sum_out=0, pass_through_ratio=0.0, retention_ratio=1.0))
        self.assertEqual(role, "terminal")
        role, _, _ = _assign_one(self.row(pass_through_ratio=0.21, retention_ratio=0.79))
        self.assertNotEqual(role, "terminal")

    def test_transit_thresholds_include_endpoints(self) -> None:
        for ratio, expected in ((0.799, "peripheral"), (0.8, "transit"), (1.2, "transit"), (1.201, "peripheral")):
            with self.subTest(ratio=ratio):
                self.assertEqual(_assign_one(self.row(pass_through_ratio=ratio))[0], expected)

    def test_direction_and_component_normalization_on_a_small_graph(self) -> None:
        graph = nx.DiGraph()
        for gid, depth in ((101, 0), (102, 1), (103, 2), (201, 0), (202, 1)):
            graph.add_node(gid, depth=depth, is_seed=depth == 0)
        for source, target, amount in ((101, 102, 12_000), (102, 103, 9_000), (201, 202, 5_000)):
            graph.add_edge(source, target, sum_kzt=amount, n_tx=1, depth=graph.nodes[target]["depth"])
        metrics = calculate_metrics(graph).set_index("gid")
        self.assertEqual(metrics.loc[102, "sum_in"], 12_000)
        self.assertEqual(metrics.loc[102, "sum_out"], 9_000)
        self.assertEqual(metrics.loc[102, "pass_through_ratio"], 0.75)
        self.assertEqual(metrics.loc[102, "retention_ratio"], 0.25)
        self.assertTrue(pd.isna(metrics.loc[101, "pass_through_ratio"]))
        self.assertEqual(metrics.loc[103, "out_degree"], 0)
        self.assertEqual(sorted(metrics.groupby("component_id").size()), [2, 3])
        for total in metrics.groupby("component_id")["pagerank"].sum():
            self.assertAlmostEqual(total, 1.0, places=8)
        clusters = detect_clusters(graph)
        for cluster_id in set(clusters.values()):
            members = [gid for gid, value in clusters.items() if value == cluster_id]
            self.assertEqual(metrics.loc[members, "component_id"].nunique(), 1)


class OutputReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.output_a = Path(cls.temp.name) / "first"
        cls.output_b = Path(cls.temp.name) / "second"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.report_a = run_pipeline(DATA_DIR, cls.output_a)
            cls.report_b = run_pipeline(DATA_DIR, cls.output_b)
        cls.nodes, cls.edges, cls.transactions = load_data(DATA_DIR)
        cls.roles = pd.read_csv(cls.output_a / "nodes_roles.csv")
        cls.clusters = pd.read_csv(cls.output_a / "clusters.csv")
        cls.top = pd.read_csv(cls.output_a / "top_nodes.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_three_csvs_are_byte_identical_after_repeated_run(self) -> None:
        for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
            with self.subTest(filename=filename):
                self.assertEqual((self.output_a / filename).read_bytes(), (self.output_b / filename).read_bytes())

    def test_row_order_does_not_change_roles_clusters_or_ranking(self) -> None:
        tables = (self.nodes, self.edges, self.transactions)
        shuffled = tuple(table.sample(frac=1, random_state=7).reset_index(drop=True) for table in tables)
        output = Path(self.temp.name) / "shuffled"
        with patch("aml_graph.pipeline.load_data", return_value=shuffled):
            with contextlib.redirect_stdout(io.StringIO()):
                run_pipeline(DATA_DIR, output)
        for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
            with self.subTest(filename=filename):
                self.assertEqual((self.output_a / filename).read_bytes(), (output / filename).read_bytes())

    def test_cluster_counts_and_internal_amounts_match_sources(self) -> None:
        joined = self.roles.merge(self.nodes, on="gid", validate="one_to_one")
        indexed_clusters = self.clusters.set_index("cluster_id")
        self.assertTrue(self.clusters["cluster_id"].is_unique)
        for cluster_id, group in joined.groupby("cluster_id"):
            with self.subTest(cluster_id=cluster_id):
                report = indexed_clusters.loc[cluster_id]
                gids = set(group["gid"])
                internal = self.edges.loc[self.edges["src"].isin(gids) & self.edges["dst"].isin(gids)]
                self.assertEqual(report["n_nodes"], len(gids))
                self.assertEqual(report["n_seed"], int(group["is_seed"].sum()))
                self.assertEqual(report["sum_kzt_internal"], int(internal["sum_kzt"].sum()))
                self.assertTrue({int(gid) for gid in str(report["top_gids"]).split(",")} <= gids)
        self.assertEqual(int(self.clusters["n_seed"].sum()), 81)

    def test_clusters_do_not_cross_disconnected_components(self) -> None:
        graph = build_graph(self.nodes, self.edges)
        component_by_gid = {gid: index for index, members in enumerate(nx.weakly_connected_components(graph)) for gid in members}
        frame = self.roles.assign(component=self.roles["gid"].map(component_by_gid))
        self.assertTrue((frame.groupby("cluster_id")["component"].nunique() == 1).all())

    def test_ranked_nodes_have_consistent_scores_and_explanations(self) -> None:
        self.assertEqual(self.top["rank"].tolist(), list(range(1, len(self.top) + 1)))
        self.assertTrue(self.top["gid"].is_unique)
        self.assertTrue(self.top["priority_score"].is_monotonic_decreasing)
        comparison = self.top.merge(self.roles, on="gid", suffixes=("_top", "_all"), validate="one_to_one")
        self.assertEqual(len(comparison), len(self.top))
        self.assertTrue((comparison["role_top"] == comparison["role_all"]).all())
        self.assertTrue((comparison["priority_score_top"] == comparison["priority_score_all"]).all())
        self.assertTrue((comparison["why"] != comparison["evidence"]).all())
        unranked = self.roles.loc[~self.roles["gid"].isin(self.top["gid"])]
        self.assertGreaterEqual(self.top["priority_score"].min(), unranked["priority_score"].max())
        self.assertTrue(set(self.roles["role"]) <= ROLES)


if __name__ == "__main__":
    unittest.main()
