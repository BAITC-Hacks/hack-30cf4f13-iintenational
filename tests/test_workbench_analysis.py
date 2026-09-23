"""Generic import analysis, observation limits, exact exports and strict parity."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from aml_graph.config import DATA_DIR
from aml_graph.pipeline import run_pipeline
from workbench.analysis import run_analysis, validate_request
from workbench.models import ImportFailure


class WorkbenchAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = {"profile": "generic", "currency": "USD", "decimals": 2,
                       "coverage": "unknown", "max_depth": None, "top_n": 30}

    def source(self, kind, content, mapping=None, name=None):
        filename = name or f"{kind}.csv"
        path = self.directory / filename
        path.write_text(content, encoding="utf-8")
        if mapping is None:
            columns = content.splitlines()[0].split(",")
            mapping = {column: column for column in columns}
        return {"kind": kind, "path": path, "filename": filename, "mapping": mapping, "options": {}}

    def transactions(self):
        return self.source("transactions", "src,dst,amount\n001,A-2,1000.00\nA-2,B,250.25\n")

    def analyze(self, sources, config=None, name="output"):
        output = self.directory / name
        report = run_analysis(sources, self.config if config is None else config, output)
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        return report, result, output

    def payload(self, output):
        html = (output / "graph_view.html").read_text(encoding="utf-8")
        return json.loads(re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', html, re.S).group(1))

    def observed_sources(self, *, depth=2, unknown_seed=False):
        nodes = self.source("nodes", f"gid,depth,is_seed\nA,0,true\nB,1,{' ' if unknown_seed else 'false'}\nC,{depth},false\nD,1,false\n".replace(", \n", ",\n"))
        edges = self.source("edges", "src,dst,amount\nA,B,100\nB,C,100\nA,D,40\n")
        return [nodes, edges]

    def test_single_file_has_exact_totals_identifiers_and_small_top(self):
        source = self.transactions()
        preview = validate_request([source], self.config)
        self.assertEqual(preview["counts"], {"nodes": 3, "edges": 2, "transactions": 2, "components": 1, "seeds": None})
        self.assertEqual(preview["amount_total"], "1250.25")
        self.assertEqual(preview["amount_total_minor"], "125025")
        report, result, output = self.analyze([source])
        self.assertEqual({node["gid"] for node in result["nodes"]}, {"001", "A-2", "B"})
        self.assertEqual(report["amount_total"], "1250.25")
        self.assertEqual(report["amount_total_minor"], "125025")
        self.assertEqual(report["counts"]["top_nodes"], 3)
        self.assertTrue(all(node["depth"] is None and node["is_seed"] is None for node in result["nodes"]))
        self.assertEqual(report["sources"][0]["sha256"], hashlib.sha256(source["path"].read_bytes()).hexdigest())
        self.assertEqual(report["sources"][0]["mapping"], source["mapping"])
        for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv", "report.json", "result.json", "graph_view.html"):
            self.assertTrue((output / filename).is_file())
        payload = self.payload(output)
        self.assertIsNone(payload["meta"]["seeds"])
        self.assertTrue(all(node["depth"] is None and node["seed"] is None for node in payload["nodes"]))
        self.assertEqual(payload["meta"]["currency"], "USD")
        self.assertEqual(payload["meta"]["moneyScale"], 2)
        self.assertIn("максимальный процентиль", payload["meta"]["priorityDescription"])
        self.assertIn("шкале 0–1", payload["meta"]["priorityDescription"])
        self.assertNotIn("умножается", payload["meta"]["priorityDescription"])

    def test_equivalent_three_tables_preserve_csv_results(self):
        tx = self.transactions()
        _, first, output1 = self.analyze([tx], name="single")
        nodes = self.source("nodes", "gid\n001\nA-2\nB\n")
        edges = self.source("edges", "src,dst,amount,n_tx\n001,A-2,1000.00,1\nA-2,B,250.25,1\n")
        _, second, output2 = self.analyze([nodes, edges, tx], name="tables")
        self.assertEqual(first, second)
        for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
            self.assertEqual((output1 / name).read_bytes(), (output2 / name).read_bytes())

    def test_all_six_formats_produce_identical_analysis(self):
        records = [{"src": "001", "dst": "A-2", "amount": "1000.00"},
                   {"src": "A-2", "dst": "B", "amount": "250.25"}]
        table = pd.DataFrame(records)
        baseline = None
        for extension in ("csv", "tsv", "xlsx", "json", "jsonl", "parquet"):
            with self.subTest(extension=extension):
                path = self.directory / f"input.{extension}"
                if extension in ("csv", "tsv"):
                    table.to_csv(path, index=False, sep="\t" if extension == "tsv" else ",")
                elif extension == "xlsx":
                    table.to_excel(path, index=False)
                elif extension == "json":
                    path.write_text(json.dumps(records), encoding="utf-8")
                elif extension == "jsonl":
                    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
                else:
                    table.to_parquet(path, index=False)
                source = {"kind": "transactions", "path": path, "filename": path.name,
                          "mapping": {column: column for column in table.columns}, "options": {}}
                report, result, output = self.analyze([source], name=f"out_{extension}")
                actual = (result, tuple((output / filename).read_bytes() for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv")))
                if baseline is None:
                    baseline = actual
                self.assertEqual(actual, baseline)
                self.assertEqual(report["amount_total"], "1250.25")

    def test_unknown_coverage_disables_transit_terminal_even_with_known_depth(self):
        _, result, _ = self.analyze(self.observed_sources())
        self.assertFalse({node["role"] for node in result["nodes"]} & {"transit", "terminal"})
        self.assertTrue(all("depth=4" not in node["evidence"] for node in result["nodes"]))

    def test_declared_second_depth_boundary_is_not_terminal(self):
        config = {**self.config, "coverage": "outward", "max_depth": 2}
        report, result, output = self.analyze(self.observed_sources(), config)
        nodes = {node["gid"]: node for node in result["nodes"]}
        self.assertEqual(nodes["B"]["role"], "transit")
        self.assertEqual(nodes["D"]["role"], "terminal")
        self.assertEqual(nodes["C"]["role"], "peripheral")
        self.assertIn("depth=2", nodes["C"]["evidence"])
        self.assertNotIn("depth=4", json.dumps(result, ensure_ascii=False))
        self.assertEqual(next(item["status"] for item in report["capabilities"] if item["id"] == "flow_roles"), "completed")
        payload = self.payload(output)
        self.assertTrue(next(node["boundary"] for node in payload["nodes"] if node["id"] == "C"))
        self.assertFalse(next(node["boundary"] for node in payload["nodes"] if node["id"] == "B"))
        self.assertIn("depth=2 при out_degree=0 итог умножается на 0,85", payload["meta"]["priorityDescription"])

    def test_depth_four_not_artificial_boundary_when_declared_depth_five(self):
        config = {**self.config, "coverage": "outward", "max_depth": 5}
        _, result, _ = self.analyze(self.observed_sources(depth=4), config)
        node = next(node for node in result["nodes"] if node["gid"] == "C")
        self.assertEqual(node["role"], "terminal")
        self.assertIn("не на границе depth=5", node["evidence"])

    def test_unknown_seed_disables_flow_rule_for_only_that_node(self):
        config = {**self.config, "coverage": "outward", "max_depth": 2}
        _, result, output = self.analyze(self.observed_sources(unknown_seed=True), config)
        nodes = {node["gid"]: node for node in result["nodes"]}
        self.assertEqual(nodes["B"]["role"], "peripheral")
        self.assertIsNone(nodes["B"]["is_seed"])
        self.assertEqual(nodes["D"]["role"], "terminal")
        cluster = pd.read_csv(output / "clusters.csv")
        unknown_cluster = nodes["B"]["cluster_id"]
        self.assertTrue(pd.isna(cluster.set_index("cluster_id").loc[unknown_cluster, "n_seed"]))

    def test_edges_only_keep_unknown_transaction_count_and_isolated_node(self):
        edges = self.source("edges", "src,dst,amount\nA,B,0.01\n")
        nodes = self.source("nodes", "gid\nA\nB\nISOLATED\n")
        report, result, output = self.analyze([edges, nodes])
        self.assertEqual(report["counts"]["components"], 2)
        self.assertIsNone(report["counts"]["transactions"])
        self.assertIsNone(result["edges"][0]["n_tx"])
        self.assertIsNone(self.payload(output)["edges"][0]["count"])
        self.assertEqual({node["gid"] for node in result["nodes"]}, {"A", "B", "ISOLATED"})
        self.assertTrue(pd.read_csv(output / "clusters.csv")["n_seed"].isna().all())

    def test_money_above_javascript_safe_integer_stays_exact(self):
        edge = self.source("edges", "src,dst,amount\n001,A-2,90071992547409.93\n")
        report, result, output = self.analyze([edge])
        self.assertEqual(report["amount_total_minor"], "9007199254740993")
        self.assertEqual(result["edges"][0]["amount"], "90071992547409.93")
        payload = self.payload(output)
        self.assertEqual(payload["edges"][0]["v"], "9007199254740993")
        self.assertEqual(payload["meta"]["total"], "9007199254740993")
        clusters = pd.read_csv(output / "clusters.csv", dtype=str)
        self.assertEqual(clusters.iloc[0]["amount_internal"], "90071992547409.93")

    def test_transaction_count_above_javascript_safe_integer_is_text(self):
        edge = self.source("edges", "src,dst,amount,n_tx\nA,B,1.00,9007199254740993\n")
        _, result, _ = self.analyze([edge])
        self.assertEqual(result["edges"][0]["n_tx"], "9007199254740993")

    def test_seed_with_income_does_not_invent_incomplete_coverage(self):
        nodes = self.source("nodes", "gid,is_seed\nA,true\nB,false\n")
        edge = self.source("edges", "src,dst,amount\nB,A,1.00\n")
        _, result, _ = self.analyze([nodes, edge])
        seed = next(node for node in result["nodes"] if node["gid"] == "A")
        self.assertEqual(seed["role"], "peripheral")
        self.assertIn("полнота внешних потоков неизвестна", seed["evidence"])
        self.assertNotIn("неполн", seed["evidence"])

    def test_isolated_seed_does_not_invent_incomplete_coverage(self):
        nodes = self.source("nodes", "gid,is_seed\nA,false\nB,false\nISOLATED,true\n")
        edge = self.source("edges", "src,dst,amount\nA,B,1.00\n")
        _, result, _ = self.analyze([nodes, edge])
        seed = next(node for node in result["nodes"] if node["gid"] == "ISOLATED")
        self.assertEqual(seed["role"], "peripheral")
        self.assertIn("полнота внешних потоков неизвестна", seed["evidence"])
        self.assertNotIn("неполн", seed["evidence"])

    def test_csv_formula_protection_is_export_only(self):
        tx = self.source("transactions", 'src,dst,amount\n=2+2,@SUM(1),1.00\n')
        report, result, output = self.analyze([tx])
        self.assertEqual({node["gid"] for node in result["nodes"]}, {"=2+2", "@SUM(1)"})
        exported = pd.read_csv(output / "nodes_roles.csv", dtype=str)
        self.assertEqual(set(exported["gid"]), {"'=2+2", "'@SUM(1)"})
        self.assertEqual({node["id"] for node in self.payload(output)["nodes"]}, {"=2+2", "@SUM(1)"})
        self.assertEqual(report["csv_policy"]["formula_id_prefix"], "'")

    def test_csv_formula_encoding_cannot_merge_distinct_quoted_ids(self):
        tx = self.source("transactions", "src,dst,amount\n=foo,'=foo,1.00\n'=foo,''=foo,1.00\n")
        report, result, output = self.analyze([tx])
        canonical = {"=foo", "'=foo", "''=foo"}
        encoded = {"'=foo", "''=foo", "'''=foo"}
        self.assertEqual({node["gid"] for node in result["nodes"]}, canonical)
        self.assertEqual({node["id"] for node in self.payload(output)["nodes"]}, canonical)
        for filename in ("nodes_roles.csv", "top_nodes.csv"):
            exported = pd.read_csv(output / filename, dtype=str)
            self.assertTrue(exported["gid"].is_unique)
            self.assertEqual(set(exported["gid"]), encoded)
            self.assertEqual({value[1:] if value.startswith("'") else value for value in exported["gid"]}, canonical)
        self.assertEqual(report["csv_policy"]["id_encoding"], "apostrophe-prefix-v1")
        self.assertIn("'", report["csv_policy"]["id_prefix_triggers"])

    def test_top_limit_can_exceed_legacy_thirty_and_is_deterministic(self):
        lines = ["src,dst,amount"] + [f"N{number:03},N{number+1:03},1.00" for number in range(40)]
        edges = self.source("edges", "\n".join(lines) + "\n")
        config = {**self.config, "top_n": 35}
        report, _, output1 = self.analyze([edges], config, name="first")
        _, _, output2 = self.analyze([edges], config, name="second")
        self.assertEqual(report["counts"]["top_nodes"], 35)
        for name in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
            self.assertEqual((output1 / name).read_bytes(), (output2 / name).read_bytes())

    def test_invalid_profile_and_inconsistent_config_are_rejected(self):
        source = self.transactions()
        invalid = [{"profile": "unknown"}, {"coverage": "outward"}, {"max_depth": 4},
                   {"decimals": -1}, {"decimals": True}, {"top_n": 0}, {"currency": "USD<script>"}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ImportFailure):
                validate_request([source], {**self.config, **change})

    def test_results_do_not_overwrite_an_existing_run(self):
        source = self.transactions()
        _, _, output = self.analyze([source])
        original = (output / "report.json").read_bytes()
        with self.assertRaises(ImportFailure):
            run_analysis([source], self.config, output)
        self.assertEqual((output / "report.json").read_bytes(), original)

    def test_strict_profile_retains_original_csv_bytes(self):
        sources = []
        for kind in ("nodes", "edges", "transactions"):
            columns = pd.read_parquet(DATA_DIR / f"{kind}.parquet").columns
            sources.append({"kind": kind, "path": DATA_DIR / f"{kind}.parquet", "filename": f"{kind}.parquet",
                            "mapping": {"amount" if field == "sum_kzt" else field: field for field in columns}, "options": {}})
        baseline = self.directory / "baseline"
        with contextlib.redirect_stdout(io.StringIO()):
            run_pipeline(DATA_DIR, baseline)
            report, result, output = self.analyze(sources, {"profile": "hackalem"})
        self.assertEqual(report["counts"]["nodes"], 2248)
        self.assertEqual(report["counts"]["components"], 16)
        self.assertEqual(report["profile"], "hackalem")
        self.assertEqual(report["config"]["max_depth"], 4)
        self.assertEqual(len(result["nodes"]), 2248)
        for filename in ("nodes_roles.csv", "clusters.csv", "top_nodes.csv"):
            self.assertEqual((baseline / filename).read_bytes(), (output / filename).read_bytes())

    def test_strict_profile_rejects_small_generic_dataset(self):
        with self.assertRaises(ImportFailure):
            validate_request([self.transactions()], {"profile": "hackalem"})


if __name__ == "__main__":
    unittest.main()
