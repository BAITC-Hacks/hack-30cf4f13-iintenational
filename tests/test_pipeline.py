from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import pandas as pd

from aml_graph.config import DATA_DIR
from aml_graph.pipeline import run_pipeline


class PipelineAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.temp_dir.name)
        started = time.perf_counter()
        cls.report = run_pipeline(DATA_DIR, cls.output_dir)
        cls.elapsed = time.perf_counter() - started
        cls.nodes_roles = pd.read_csv(cls.output_dir / "nodes_roles.csv")
        cls.clusters = pd.read_csv(cls.output_dir / "clusters.csv")
        cls.top_nodes = pd.read_csv(cls.output_dir / "top_nodes.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_pipeline_finishes_under_five_minutes(self) -> None:
        self.assertLess(self.elapsed, 300)

    def test_all_nodes_have_explained_roles(self) -> None:
        self.assertEqual(len(self.nodes_roles), 2_248)
        self.assertTrue(self.nodes_roles["gid"].is_unique)
        self.assertFalse(self.nodes_roles.isna().any().any())
        self.assertTrue((self.nodes_roles["evidence"].str.len() <= 200).all())

    def test_depth_four_is_not_called_terminal(self) -> None:
        nodes = pd.read_parquet(DATA_DIR / "nodes.parquet")
        joined = self.nodes_roles.merge(nodes[["gid", "depth"]], on="gid", how="left")
        self.assertFalse(((joined["depth"] == 4) & (joined["role"] == "terminal")).any())

    def test_cluster_ids_are_consistent(self) -> None:
        self.assertEqual(set(self.nodes_roles["cluster_id"]), set(self.clusters["cluster_id"]))
        self.assertEqual(int(self.clusters["n_nodes"].sum()), 2_248)

    def test_top_list_and_view_exist(self) -> None:
        self.assertGreaterEqual(len(self.top_nodes), 20)
        view = self.output_dir / "graph_view.html"
        self.assertTrue(view.is_file())
        html = view.read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy", html)
        self.assertIn('id="graph-data" type="application/json"', html)
        self.assertIn("${esc(n.evidence)}", html)

    def test_cautious_language(self) -> None:
        text = " ".join(
            self.nodes_roles["evidence"].tolist()
            + self.clusters["hypothesis"].tolist()
            + self.top_nodes["why"].tolist()
        ).lower()
        for forbidden in ("виновен", "преступник", "доказано", "точно участвует"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
