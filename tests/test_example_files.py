"""Exercise the actual downloadable synthetic examples, not regenerated copies."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from workbench.analysis import run_analysis, validate_request
from workbench.importing import inspect_file, prepare_dataset
from workbench.models import FORMATS, ImportFailure


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "workbench"
EXTENSIONS = ("csv", "tsv", "xlsx", "json", "jsonl", "parquet")
FIELDS = {
    "transactions": ("src", "dst", "amount", "date"),
    "edges": ("src", "dst", "amount", "n_tx"),
    "nodes": ("gid",),
}
CONFIG = {"profile": "generic", "currency": "USD", "decimals": 2,
          "coverage": "unknown", "max_depth": None, "top_n": 30}
CSV_ARTIFACTS = ("nodes_roles.csv", "clusters.csv", "top_nodes.csv")
EXPECTED_ARTIFACTS = (*CSV_ARTIFACTS, "report.json", "result.json", "graph_view.html")


class ExampleFilesTests(unittest.TestCase):
    def source(self, kind, extension):
        path = EXAMPLES / "formats" / extension / f"{kind}.{extension}"
        self.assertTrue(path.is_file(), f"Missing committed example: {path}")
        return {"kind": kind, "path": path, "filename": path.name,
                "mapping": {field: field for field in FIELDS[kind]}, "options": {}}

    def tables(self, extension):
        return [self.source(kind, extension) for kind in ("nodes", "edges", "transactions")]

    def invalid_source(self, filename, fields=FIELDS["transactions"]):
        path = EXAMPLES / "invalid" / filename
        self.assertTrue(path.is_file(), f"Missing committed negative example: {path}")
        return {"kind": "transactions", "path": path, "filename": path.name,
                "mapping": {field: field for field in fields}, "options": {}}

    def assert_summary(self, summary, *, nodes, components):
        self.assertEqual(summary["counts"], {"nodes": nodes, "edges": 2,
                                          "transactions": 2, "components": components,
                                          "seeds": None})
        self.assertEqual(summary["amount_total"], "1250.25")
        self.assertEqual(summary["amount_total_minor"], "125025")

    def assert_import_issue(self, source, field, message_fragment=None):
        # These files are syntactically readable; rejection belongs to validation.
        preview = inspect_file(source["path"], source["filename"], source["options"])
        self.assertGreater(preview["rows"], 0)
        with self.assertRaises(ImportFailure) as caught:
            validate_request([source], CONFIG)
        issues = caught.exception.issues
        self.assertTrue(issues)
        matching = [issue for issue in issues
                    if issue["table"] == "transactions" and issue["field"] == field]
        self.assertTrue(matching, issues)
        if field is not None:
            self.assertTrue(all(isinstance(issue["row"], int) and issue["row"] >= 1
                                for issue in matching))
        if message_fragment is not None:
            self.assertTrue(any(message_fragment in issue["message"] for issue in matching), issues)

    def test_all_supported_formats_have_readable_examples_for_each_table(self):
        self.assertEqual({f".{extension}" for extension in EXTENSIONS}, set(FORMATS))
        for extension in EXTENSIONS:
            for kind, fields in FIELDS.items():
                with self.subTest(extension=extension, kind=kind):
                    source = self.source(kind, extension)
                    preview = inspect_file(source["path"], source["filename"])
                    self.assertEqual(preview["columns"], list(fields))
                    self.assertEqual(preview["rows"], 4 if kind == "nodes" else 2)
                    self.assertEqual(preview["sheets"], ["Данные"] if extension == "xlsx" else [])
                    first_id = "gid" if kind == "nodes" else "src"
                    self.assertEqual(preview["preview"][0][first_id], "001")
                    json.dumps(preview, allow_nan=False)

    def test_transaction_only_examples_derive_three_nodes_and_exact_money(self):
        for extension in EXTENSIONS:
            with self.subTest(extension=extension):
                sources = [self.source("transactions", extension)]
                self.assert_summary(validate_request(sources, CONFIG), nodes=3, components=1)
                dataset = prepare_dataset(sources, CONFIG)
                self.assertEqual(dataset.nodes.gid.tolist(), ["001", "A-2", "B"])
                self.assertEqual(list(zip(dataset.transactions.src, dataset.transactions.dst)),
                                 [("001", "A-2"), ("A-2", "B")])
                self.assertEqual(dataset.transactions.amount_minor.tolist(), [100000, 25025])
                self.assertEqual(dataset.transactions.date.tolist(),
                                 [dt.datetime(2026, 7, 1), dt.datetime(2026, 7, 2)])
                self.assertTrue(dataset.nodes.depth.isna().all())
                self.assertTrue(dataset.nodes.is_seed.isna().all())

    def test_three_table_examples_are_equivalent_and_preserve_isolated_node(self):
        original_sources = []
        for kind in ("nodes", "edges", "transactions"):
            path = EXAMPLES / f"{kind}.{'json' if kind == 'nodes' else 'csv'}"
            original_sources.append({"kind": kind, "path": path, "filename": path.name,
                                     "mapping": {field: field for field in FIELDS[kind]}, "options": {}})
        baseline = prepare_dataset(original_sources, CONFIG)
        for extension in EXTENSIONS:
            with self.subTest(extension=extension):
                sources = self.tables(extension)
                self.assert_summary(validate_request(sources, CONFIG), nodes=4, components=2)
                dataset = prepare_dataset(sources, CONFIG)
                self.assertEqual(set(dataset.nodes.gid), {"001", "A-2", "B", "ISOLATED"})
                self.assertEqual(dataset.edges.n_tx.tolist(), [1, 1])
                self.assertTrue(dataset.nodes.depth.isna().all())
                self.assertTrue(dataset.nodes.is_seed.isna().all())
                for table in ("nodes", "edges", "transactions"):
                    pd.testing.assert_frame_equal(getattr(dataset, table), getattr(baseline, table))

    def test_edges_and_nodes_do_not_invent_individual_transactions(self):
        for extension in EXTENSIONS:
            with self.subTest(extension=extension):
                sources = [self.source("nodes", extension), self.source("edges", extension)]
                summary = validate_request(sources, CONFIG)
                self.assertEqual(summary["counts"], {"nodes": 4, "edges": 2,
                                                    "transactions": None, "components": 2,
                                                    "seeds": None})
                self.assertEqual(summary["amount_total"], "1250.25")
                self.assertEqual(summary["amount_total_minor"], "125025")
                dataset = prepare_dataset(sources, CONFIG)
                self.assertIsNone(dataset.transactions)
                self.assertEqual(int(dataset.edges.n_tx.sum()), 2)

    def test_each_format_produces_all_artifacts_and_identical_csv_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            baseline = None
            for extension in EXTENSIONS:
                with self.subTest(extension=extension):
                    output = Path(temporary) / extension
                    report = run_analysis(self.tables(extension), CONFIG, output)
                    self.assertEqual(report["counts"]["nodes"], 4)
                    self.assertEqual(report["counts"]["components"], 2)
                    self.assertEqual(report["amount_total_minor"], "125025")
                    for filename in EXPECTED_ARTIFACTS:
                        self.assertTrue((output / filename).is_file(), filename)
                        self.assertGreater((output / filename).stat().st_size, 0, filename)
                    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
                    self.assertEqual({node["gid"] for node in result["nodes"]},
                                     {"001", "A-2", "B", "ISOLATED"})
                    self.assertTrue(all(node["depth"] is None and node["is_seed"] is None
                                        for node in result["nodes"]))
                    self.assertFalse({node["role"] for node in result["nodes"]}
                                     & {"terminal", "transit"})
                    actual = tuple((output / filename).read_bytes() for filename in CSV_ARTIFACTS)
                    if baseline is None:
                        baseline = actual
                    self.assertEqual(actual, baseline)

    def test_mixed_format_tables_work_together(self):
        sources = [self.source("nodes", "json"), self.source("edges", "parquet"),
                   self.source("transactions", "xlsx")]
        self.assert_summary(validate_request(sources, CONFIG), nodes=4, components=2)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "mixed"
            run_analysis(sources, CONFIG, output)
            reference = Path(temporary) / "csv"
            run_analysis(self.tables("csv"), CONFIG, reference)
            for filename in CSV_ARTIFACTS:
                self.assertEqual((output / filename).read_bytes(), (reference / filename).read_bytes())

    def test_negative_amount_example_is_rejected_during_validation(self):
        self.assert_import_issue(self.invalid_source("negative_amount.csv"), "amount")

    def test_self_transfer_example_is_rejected_during_validation(self):
        self.assert_import_issue(self.invalid_source("self_transfer.tsv"), "dst", "Петли")

    def test_formula_example_is_rejected_before_preview(self):
        source = self.invalid_source("formula.xlsx")
        with self.assertRaises(ImportFailure) as caught:
            inspect_file(source["path"], source["filename"])
        self.assertIn("Формулы", str(caught.exception))
        self.assertEqual(caught.exception.issues[0]["field"], "amount")
        self.assertEqual(caught.exception.issues[0]["row"], 1)

    def test_mixed_currency_example_is_rejected_without_conversion(self):
        fields = (*FIELDS["transactions"], "currency")
        self.assert_import_issue(self.invalid_source("mixed_currencies.json", fields),
                                 "currency", "Валюта")

    def test_invalid_date_example_is_rejected_during_validation(self):
        self.assert_import_issue(self.invalid_source("invalid_date.jsonl"), "date")

    def test_missing_sender_example_requires_mapping_before_analysis(self):
        source = self.invalid_source("missing_sender.parquet", ("dst", "amount", "date"))
        preview = inspect_file(source["path"], source["filename"])
        self.assertNotIn("src", preview["columns"])
        self.assert_import_issue(source, None, "обязательные поля")
        # A caller also cannot bypass the missing-column check with a stale mapping.
        source["mapping"]["src"] = "src"
        self.assert_import_issue(source, None, "отсутствующую колонку")


if __name__ == "__main__":
    unittest.main()
