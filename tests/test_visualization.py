"""Проверки автономной HTML-выгрузки и безопасной передачи данных в браузер."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import networkx as nx
import pandas as pd

from aml_graph.clustering import build_clusters_table, detect_clusters
from aml_graph.config import DATA_DIR
from aml_graph.exports import build_nodes_roles, build_top_nodes
from aml_graph.metrics import calculate_metrics
from aml_graph.roles import assign_roles, calculate_priority
from aml_graph.visualization import _script_json, demo_source_matches, write_graph_view
from starter.data_loader import load_data
from starter.export_templates import CLUSTERS_COLUMNS, NODES_ROLES_COLUMNS, TOP_NODES_COLUMNS
from starter.graph_builder import build_graph


def explained_frame(graph: nx.DiGraph) -> pd.DataFrame:
    frame = assign_roles(calculate_metrics(graph))
    frame["cluster_id"] = frame["gid"].map(detect_clusters(graph)).astype(int)
    return calculate_priority(frame)


def extract_payload(document: str) -> dict:
    match = re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', document, re.DOTALL)
    if match is None:
        raise AssertionError("В HTML отсутствует единый JSON-пакет данных")
    return json.loads(match.group(1))


class ScriptJsonSafetyTests(unittest.TestCase):
    def test_script_closing_tags_and_unicode_separators_are_encoded(self) -> None:
        value = {"evidence": '</script><script>alert("x")</script>&\u2028\u2029'}
        encoded = _script_json(value)
        self.assertNotIn("<", encoded)
        self.assertNotIn(">", encoded)
        self.assertNotIn("&", encoded)
        self.assertNotIn("\u2028", encoded)
        self.assertNotIn("\u2029", encoded)
        self.assertIn("\\u2028", encoded)
        self.assertIn("\\u2029", encoded)
        self.assertEqual(json.loads(encoded), value)

    def test_non_finite_numbers_fail_instead_of_emitting_invalid_json(self) -> None:
        for number in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(number=number), self.assertRaises(ValueError):
                _script_json({"priority": number})


class StandaloneViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        nodes, edges, _ = load_data(DATA_DIR)
        cls.graph = build_graph(nodes, edges)
        cls.frame = explained_frame(cls.graph)
        cls.clusters = build_clusters_table(cls.graph, cls.frame)
        cls.top_nodes = build_top_nodes(cls.frame)
        cls.temp = tempfile.TemporaryDirectory()
        cls.view_path = Path(cls.temp.name) / "graph.html"
        write_graph_view(cls.view_path, cls.graph, cls.frame, clusters=cls.clusters, top_nodes=cls.top_nodes, is_demo=True)
        cls.document = cls.view_path.read_text(encoding="utf-8")
        cls.payload = extract_payload(cls.document)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_downloads_have_exact_complete_export_schemas_and_rows(self) -> None:
        expected = {
            "nodes_roles.csv": (build_nodes_roles(self.frame), NODES_ROLES_COLUMNS),
            "clusters.csv": (self.clusters, CLUSTERS_COLUMNS),
            "top_nodes.csv": (self.top_nodes, TOP_NODES_COLUMNS),
        }
        self.assertEqual(set(self.payload["downloads"]), set(expected))
        for filename, (frame, columns) in expected.items():
            with self.subTest(filename=filename):
                embedded = pd.read_csv(io.StringIO(self.payload["downloads"][filename]))
                original = pd.read_csv(io.StringIO(frame.to_csv(index=False)))
                self.assertEqual(list(embedded.columns), columns)
                pd.testing.assert_frame_equal(embedded, original)

    def test_view_contains_all_directed_edges_and_nodes(self) -> None:
        self.assertEqual(len(self.payload["nodes"]), self.graph.number_of_nodes())
        expected = {(str(source), str(target), int(attrs["sum_kzt"]), int(attrs["n_tx"])) for source, target, attrs in self.graph.edges(data=True)}
        observed = {(edge["s"], edge["t"], edge["v"], edge["count"]) for edge in self.payload["edges"]}
        self.assertEqual(observed, expected)
        self.assertEqual(self.payload["meta"]["components"], 16)
        self.assertEqual(self.payload["meta"]["seeds"], 81)
        self.assertEqual(self.payload["meta"]["total"], 365_890_012)

    def test_document_is_offline_and_has_no_unresolved_placeholders(self) -> None:
        for placeholder in ("/*__STYLE__*/", "/*__SCRIPT__*/", "__DATA_JSON__"):
            self.assertNotIn(placeholder, self.document)
        self.assertIn("connect-src 'none'", self.document)
        self.assertIn("object-src 'none'", self.document)
        self.assertIn("base-uri 'none'", self.document)
        self.assertIn("form-action 'none'", self.document)
        self.assertIsNone(re.search(r'<(?:script|link|img)\b[^>]*(?:src|href)\s*=\s*[\"\']https?://', self.document, re.IGNORECASE))

    def test_malicious_evidence_cannot_create_a_script_element(self) -> None:
        frame = self.frame.copy(deep=True)
        malicious = '</script><script id="injected">alert(1)</script>\u2028&'
        frame.loc[0, "evidence"] = malicious
        output = Path(self.temp.name) / "untrusted.html"
        write_graph_view(output, self.graph, frame, clusters=self.clusters, top_nodes=self.top_nodes)
        document = output.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"<script\b", document, re.IGNORECASE)), 2)
        self.assertNotIn('<script id="injected">', document)
        payload = extract_payload(document)
        expected_gid = str(int(frame.loc[0, "gid"]))
        self.assertEqual(next(node["evidence"] for node in payload["nodes"] if node["id"] == expected_gid), malicious)

    def test_int64_identifiers_above_javascript_precision_remain_distinct(self) -> None:
        gids = [2**53, 2**53 + 1, 2**63 - 1]
        graph = nx.DiGraph()
        for index, gid in enumerate(gids):
            graph.add_node(gid, depth=index, is_seed=index == 0)
        for source, target in zip(gids, gids[1:]):
            graph.add_edge(source, target, sum_kzt=10_000, n_tx=1, depth=graph.nodes[target]["depth"])
        output = Path(self.temp.name) / "large-identifiers.html"
        write_graph_view(output, graph, explained_frame(graph))
        payload = extract_payload(output.read_text(encoding="utf-8"))
        self.assertEqual([node["id"] for node in payload["nodes"]], [str(gid) for gid in gids])
        self.assertEqual([(edge["s"], edge["t"]) for edge in payload["edges"]], [(str(a), str(b)) for a, b in zip(gids, gids[1:])])
        csv_nodes = pd.read_csv(io.StringIO(payload["downloads"]["nodes_roles.csv"]), dtype={"gid": str})
        self.assertEqual(csv_nodes["gid"].tolist(), [str(gid) for gid in gids])


class DemoManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.hashes = {}
        for name in ("nodes.parquet", "edges.parquet", "transactions.parquet"):
            content = f"test bytes for {name}".encode("utf-8")
            (self.directory / name).write_bytes(content)
            self.hashes[name] = hashlib.sha256(content).hexdigest()
        self.manifest_path = self.directory / "demo_manifest.json"

    def write_manifest(self, value: object) -> None:
        self.manifest_path.write_text(json.dumps(value), encoding="utf-8")

    def test_exact_manifest_is_recognized(self) -> None:
        self.write_manifest({"kind": "synthetic", "sha256": self.hashes})
        self.assertTrue(demo_source_matches(self.directory))

    def test_changed_file_is_not_mislabeled_as_verified_demo(self) -> None:
        self.write_manifest({"kind": "synthetic", "sha256": self.hashes})
        (self.directory / "nodes.parquet").write_bytes(b"different input")
        self.assertFalse(demo_source_matches(self.directory))

    def test_missing_or_malformed_manifest_fails_closed(self) -> None:
        self.assertFalse(demo_source_matches(self.directory))
        self.manifest_path.write_text("{ broken JSON", encoding="utf-8")
        self.assertFalse(demo_source_matches(self.directory))
        for value in (None, [], "text", 4, {}, {"kind": "real", "sha256": self.hashes}, {"kind": "synthetic", "sha256": {}}):
            with self.subTest(manifest=value):
                self.write_manifest(value)
                self.assertFalse(demo_source_matches(self.directory))


if __name__ == "__main__":
    unittest.main()
