"""Real-format import tests, exact money, nullable facts and hostile input guards."""
from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from openpyxl import Workbook

from workbench.importing import MAX_AMOUNT_MINOR, inspect_file, normalize_config, prepare_dataset
from workbench.models import ImportFailure


class ImportingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.config = {"profile": "generic", "currency": "KZT", "decimals": 2,
                       "coverage": "unknown", "max_depth": None, "top_n": 30}
        self.rows = [
            {"payer": "001", "payee": "A-2", "value": "1000.00", "date": "2026-07-01", "ccy": "KZT"},
            {"payer": "001", "payee": "A-2", "value": "250.00", "date": "2026-07-02", "ccy": "KZT"},
            {"payer": "A-2", "payee": "B", "value": "0.25", "date": "2026-07-03", "ccy": "KZT"},
        ]
        self.mapping = {"src": "payer", "dst": "payee", "amount": "value", "date": "date", "currency": "ccy"}

    def tearDown(self):
        self.temporary.cleanup()

    def file(self, rows=None, extension=".csv", name="transactions", options=None):
        rows = self.rows if rows is None else rows
        path = self.directory / (name + extension)
        if extension in (".csv", ".tsv"):
            settings = options or {}
            pd.DataFrame(rows).to_csv(path, index=False, sep=settings.get("delimiter", "\t" if extension == ".tsv" else ","),
                                      encoding=settings.get("encoding", "utf-8-sig"))
        elif extension == ".json":
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        elif extension == ".jsonl":
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
        elif extension == ".parquet":
            pd.DataFrame(rows).to_parquet(path, index=False)
        elif extension == ".xlsx":
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Переводы"
            sheet.append(list(rows[0]))
            for row in rows:
                sheet.append(list(row.values()))
            workbook.save(path)
            workbook.close()
        return path

    def source(self, path=None, kind="transactions", mapping=None, options=None):
        path = path or self.file()
        return {"path": path, "filename": path.name, "kind": kind,
                "mapping": self.mapping if mapping is None else mapping, "options": options or {}}

    def dataset(self, rows=None, extension=".csv", config=None, mapping=None, options=None):
        return prepare_dataset([self.source(self.file(rows, extension, options=options), mapping=mapping,
                                            options=options)], self.config if config is None else config)

    def assert_failure(self, callback, table=None, field=None):
        with self.assertRaises(ImportFailure) as caught:
            callback()
        self.assertTrue(caught.exception.issues)
        if table:
            self.assertEqual(caught.exception.issues[0]["table"], table)
        if field:
            self.assertEqual(caught.exception.issues[0]["field"], field)
        return caught.exception

    def test_six_real_file_formats_are_equivalent(self):
        baseline = None
        for extension in (".csv", ".tsv", ".xlsx", ".json", ".jsonl", ".parquet"):
            with self.subTest(extension=extension):
                result = self.dataset(extension=extension)
                self.assertEqual(result.nodes.gid.tolist(), ["001", "A-2", "B"])
                self.assertEqual(int(result.edges.amount_minor.sum()), 125025)
                self.assertEqual(result.edges.n_tx.tolist(), [2, 1])
                self.assertTrue(result.nodes.depth.isna().all())
                self.assertTrue(result.nodes.is_seed.isna().all())
                self.assertEqual(result.transactions.date.iloc[0], dt.datetime(2026, 7, 1))
                if baseline is not None:
                    pd.testing.assert_frame_equal(result.nodes, baseline.nodes)
                    pd.testing.assert_frame_equal(result.edges, baseline.edges)
                    pd.testing.assert_frame_equal(result.transactions, baseline.transactions)
                baseline = result

    def test_preview_is_bounded_and_json_serializable(self):
        rows = self.rows * 3
        result = inspect_file(self.file(rows), "transactions.csv")
        self.assertEqual(result["rows"], 9)
        self.assertEqual(len(result["preview"]), 5)
        self.assertEqual(result["columns"], list(self.rows[0]))
        json.dumps(result, allow_nan=False)

    def test_exact_json_large_numeric_identifiers_and_preview(self):
        rows = [{"src": 9223372036854775807, "dst": "001", "amount": "1"}]
        path = self.file(rows, ".json")
        result = prepare_dataset([self.source(path, mapping={key: key for key in rows[0]})], self.config)
        self.assertIn("9223372036854775807", result.nodes.gid.tolist())
        self.assertEqual(inspect_file(path, path.name)["preview"][0]["src"], "9223372036854775807")

    def test_json_decimal_is_not_binary_float(self):
        path = self.directory / "money.json"
        path.write_text('[{"src":"a","dst":"b","amount":12345678901234.567890}]', encoding="utf-8")
        config = {**self.config, "decimals": 6}
        # This amount is deliberately too large for safe int64 turnover.
        self.assert_failure(lambda: prepare_dataset([self.source(path, mapping={key: key for key in ("src", "dst", "amount")})], config))
        path.write_text('[{"src":"a","dst":"b","amount":1234567890.123456}]', encoding="utf-8")
        result = prepare_dataset([self.source(path, mapping={key: key for key in ("src", "dst", "amount")})], config)
        self.assertEqual(result.edges.amount_minor.iloc[0], 1234567890123456)

    def test_unknown_metadata_is_not_fabricated(self):
        mapping = {key: self.mapping[key] for key in ("src", "dst", "amount")}
        result = self.dataset(mapping=mapping)
        self.assertTrue(result.transactions.date.isna().all())
        self.assertTrue(result.nodes.depth.isna().all())
        self.assertTrue(result.nodes.is_seed.isna().all())
        self.assertEqual(result.metadata["coverage"], "unknown")
        self.assertIsNone(result.metadata["max_depth"])
        self.assertGreaterEqual(len(result.warnings), 3)

    def test_separate_tables_match_and_preserve_isolates(self):
        node_rows = [{"gid": "001", "depth": "0", "seed": "true"},
                     {"gid": "A-2", "depth": "1", "seed": "false"},
                     {"gid": "B", "depth": "2", "seed": "false"},
                     {"gid": "isolate", "depth": "", "seed": ""}]
        edge_rows = [{"src": "001", "dst": "A-2", "amount": "1250.00", "depth": "1"},
                     {"src": "A-2", "dst": "B", "amount": "0.25", "depth": "2"}]
        sources = [self.source(), self.source(self.file(node_rows, name="nodes"), "nodes",
                   {"gid": "gid", "depth": "depth", "is_seed": "seed"}),
                   self.source(self.file(edge_rows, name="edges"), "edges", {key: key for key in edge_rows[0]})]
        result = prepare_dataset(sources, self.config)
        self.assertEqual(len(result.nodes), 4)
        self.assertTrue(result.nodes.loc[result.nodes.gid == "isolate", "depth"].isna().all())
        self.assertTrue(bool(result.nodes.is_seed.iloc[0]))
        self.assertEqual(result.edges.n_tx.tolist(), [2, 1])
        self.assertEqual(result.edges.depth.tolist(), [1, 2])

    def test_edges_only_does_not_invent_transactions_or_counts(self):
        path = self.file([{"src": "001", "dst": "B", "amount": "10.25"}], name="edges")
        result = prepare_dataset([self.source(path, "edges", {key: key for key in ("src", "dst", "amount")})], self.config)
        self.assertIsNone(result.transactions)
        self.assertTrue(result.edges.n_tx.isna().all())
        self.assertEqual(result.edges.amount_minor.iloc[0], 1025)

    def test_duplicate_transactions_are_warned_and_preserved(self):
        result = self.dataset(rows=[self.rows[0], self.rows[0]])
        self.assertEqual(len(result.transactions), 2)
        self.assertEqual(result.edges.amount_minor.iloc[0], 200000)
        self.assertEqual(result.edges.n_tx.iloc[0], 2)
        self.assertTrue(any("Совпадающих" in warning["message"] for warning in result.warnings))

    def test_amount_precision_is_not_rounded(self):
        for amount in ("1.001", "0.001", "1.0000000000000000000000000000000001"):
            with self.subTest(amount=amount):
                self.assert_failure(lambda: self.dataset([{**self.rows[0], "value": amount}]), "transactions", "amount")
        result = self.dataset([{**self.rows[0], "value": "1.2300000000000000000000000000000000"}])
        self.assertEqual(result.edges.amount_minor.iloc[0], 123)

    def test_positive_finite_amounts_required(self):
        for amount in ("", "0", "-1", "NaN", "Infinity", "1,000.00", "1e99999999", True):
            with self.subTest(amount=amount):
                self.assert_failure(lambda: self.dataset([{**self.rows[0], "value": amount}], extension=".json"), "transactions", "amount")

    def test_int64_total_overflow_and_large_exact_values(self):
        config = {**self.config, "decimals": 0}
        good = self.dataset([{**self.rows[0], "value": str(MAX_AMOUNT_MINOR)}], config=config)
        self.assertEqual(int(good.edges.amount_minor.iloc[0]), MAX_AMOUNT_MINOR)
        self.assert_failure(lambda: self.dataset([{**self.rows[0], "value": str(MAX_AMOUNT_MINOR)},
                                                  {**self.rows[1], "value": "1"}], config=config), "edges", "amount")

    def test_bad_identifier_not_coerced(self):
        for bad in (None, "", " a", "a ", "1.5", 1.5, True, "a\nb"):
            with self.subTest(bad=bad):
                self.assert_failure(lambda: self.dataset([{**self.rows[0], "payer": bad}], extension=".json"), "transactions", "src")

    def test_mixed_currency_rejected_without_conversion(self):
        self.assert_failure(lambda: self.dataset([{**self.rows[0], "ccy": "USD"}]), "transactions", "currency")

    def test_explicit_delimiter_encoding_decimal_and_date(self):
        rows = [{"payer": "узел-1", "payee": "узел-2", "value": "12,25", "date": "23.09.2026", "ccy": "KZT"}]
        options = {"delimiter": ";", "encoding": "cp1251", "decimal": ",", "date_format": "%d.%m.%Y"}
        result = self.dataset(rows, options=options)
        self.assertEqual(result.edges.amount_minor.iloc[0], 1225)
        self.assertEqual(result.transactions.date.iloc[0], dt.datetime(2026, 9, 23))
        self.assertIn("узел-1", result.nodes.gid.tolist())

    def test_ambiguous_dates_rejected(self):
        for value in ("01/02/2026", "2026-13-01", "2026-09-23T01:02:03Z", "2026-09-23T01:02:03+05:00", 46000):
            with self.subTest(value=value):
                self.assert_failure(lambda: self.dataset([{**self.rows[0], "date": value}], extension=".json"), "transactions", "date")

    def test_parquet_timezone_is_rejected(self):
        rows = [{**self.rows[0], "date": dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)}]
        self.assert_failure(lambda: self.dataset(rows, extension=".parquet"), "transactions", "date")

    def test_date_time_and_absent_date_remain_exact(self):
        result = self.dataset([{**self.rows[0], "date": "2026-07-01 23:59:59.123456"},
                               {**self.rows[1], "date": ""}])
        self.assertEqual(result.transactions.date.iloc[0], dt.datetime(2026, 7, 1, 23, 59, 59, 123456))
        self.assertIsNone(result.transactions.date.iloc[1])

    def test_no_self_loops(self):
        self.assert_failure(lambda: self.dataset([{**self.rows[0], "payee": "001"}]), "transactions", "dst")

    def test_unknown_endpoints_rejected_when_nodes_supplied(self):
        nodes = self.source(self.file([{"gid": "001"}], name="nodes"), "nodes", {"gid": "gid"})
        self.assert_failure(lambda: prepare_dataset([self.source(), nodes], self.config), "edges", "dst")

    def test_duplicate_nodes_and_edges_rejected(self):
        nodes = self.source(self.file([{"gid": "001"}, {"gid": "001"}], name="nodes"), "nodes", {"gid": "gid"})
        self.assert_failure(lambda: prepare_dataset([self.source(), nodes], self.config), "nodes", "gid")
        edges = self.source(self.file([{"src": "a", "dst": "b", "amount": "1"}] * 2, name="edges"),
                            "edges", {key: key for key in ("src", "dst", "amount")})
        self.assert_failure(lambda: prepare_dataset([edges], self.config), "edges", "src")

    def test_aggregate_mismatches_rejected(self):
        for amount, count in (("1249.99", "2"), ("1250.00", "3")):
            edges = self.source(self.file([{"src": "001", "dst": "A-2", "amount": amount, "n_tx": count},
                                           {"src": "A-2", "dst": "B", "amount": "0.25", "n_tx": "1"}], name="edges"),
                                "edges", {key: key for key in ("src", "dst", "amount", "n_tx")})
            self.assert_failure(lambda: prepare_dataset([self.source(), edges], self.config), "edges")

    def test_missing_aggregate_pair_rejected(self):
        edges = self.source(self.file([{"src": "001", "dst": "A-2", "amount": "1250"}], name="edges"),
                            "edges", {key: key for key in ("src", "dst", "amount")})
        self.assert_failure(lambda: prepare_dataset([self.source(), edges], self.config), "edges")

    def test_bad_integer_or_bool_is_not_silently_cast(self):
        for field, value in (("depth", "1.5"), ("depth", "-1"), ("is_seed", "yes")):
            node = {"gid": "001", field: value}
            nodes = self.source(self.file([node], name="nodes"), "nodes", {key: key for key in node})
            self.assert_failure(lambda: prepare_dataset([self.source(), nodes], self.config), "nodes", field)

    def test_declared_boundary_must_not_be_exceeded(self):
        nodes = self.source(self.file([{"gid": "001", "depth": "3"}], name="nodes"), "nodes",
                            {"gid": "gid", "depth": "depth"})
        self.assert_failure(lambda: prepare_dataset([self.source(), nodes], {**self.config, "coverage": "outward", "max_depth": 2}), "nodes", "depth")

    def test_declared_outward_observation_contradictions_rejected(self):
        config = {**self.config, "coverage": "outward", "max_depth": 2}
        for depth, seed, expected_table in (("1", "true", "nodes"), ("0", "false", "nodes"), ("2", "false", "edges")):
            rows = [{"gid": "001", "depth": depth, "is_seed": seed},
                    {"gid": "A-2", "depth": "1", "is_seed": "false"},
                    {"gid": "B", "depth": "2", "is_seed": "false"}]
            source = self.source(self.file(rows, name="nodes"), "nodes", {key: key for key in rows[0]})
            self.assert_failure(lambda: prepare_dataset([self.source(), source], config), expected_table)

    def test_edge_depth_agrees_with_known_destination(self):
        nodes = self.source(self.file([{"gid": "a", "depth": "0"}, {"gid": "b", "depth": "1"}], name="nodes"),
                            "nodes", {"gid": "gid", "depth": "depth"})
        edges = self.source(self.file([{"src": "a", "dst": "b", "amount": "1", "depth": "2"}], name="edges"),
                            "edges", {key: key for key in ("src", "dst", "amount", "depth")})
        self.assert_failure(lambda: prepare_dataset([nodes, edges], self.config), "edges", "depth")

    def test_profile_defaults_are_shared_and_not_guessed_from_data(self):
        strict = normalize_config({"profile": "hackalem"})
        self.assertEqual(strict["coverage"], "outward")
        self.assertEqual(strict["max_depth"], 4)
        generic = normalize_config({})
        self.assertEqual(generic["coverage"], "unknown")
        self.assertIsNone(generic["max_depth"])

    def test_hackalem_requires_three_tables_and_fixed_currency(self):
        self.assert_failure(lambda: self.dataset(config={"profile": "hackalem"}))
        for config in ({"profile": "hackalem", "currency": "USD"},
                       {"profile": "hackalem", "decimals": 2},
                       {"profile": "hackalem", "top_n": 10}):
            self.assert_failure(lambda: self.dataset(config=config), "config")

    def test_unknown_configs_and_code_cannot_enter_pipeline(self):
        for config in ({"plugin": "x.py"}, {"profile": "other"}, {"decimals": True},
                       {"decimals": 7}, {"top_n": 0}, {"top_n": 501}, {"currency": "kzt"},
                       {"coverage": "outward"}, {"max_depth": 4}):
            self.assert_failure(lambda: self.dataset(config=config), "config")
        for options in ({"loader": "x.py"}, {"encoding": "utf-16"}, {"delimiter": "ab"},
                        {"decimal": ";"}, {"date_format": "%m/%d"}, {"date_format": "%Y-%m-%d %z"}):
            self.assert_failure(lambda: inspect_file(self.file(), "transactions.csv", options))

    def test_unknown_mapping_missing_field_and_reused_column(self):
        for mapping in ({"src": "payer"}, {**self.mapping, "plugin": "payer"},
                        {**self.mapping, "dst": "missing"}, {**self.mapping, "dst": "payer"}):
            self.assert_failure(lambda: self.dataset(mapping=mapping), "transactions")

    def test_file_and_table_limits_fail_without_truncation(self):
        path = self.file()
        with patch("workbench.importing.MAX_FILE_BYTES", 5):
            self.assert_failure(lambda: inspect_file(path, path.name))
        with patch("workbench.importing.MAX_ROWS", 2):
            self.assert_failure(lambda: inspect_file(path, path.name))
        with patch("workbench.importing.MAX_COLUMNS", 2):
            self.assert_failure(lambda: inspect_file(path, path.name))
        with patch("workbench.importing.MAX_CELL_CHARS", 12):
            self.assert_failure(lambda: self.dataset([{**self.rows[0], "payer": "a" * 13}]))
        with patch("workbench.importing.MAX_NODES", 2):
            self.assert_failure(lambda: self.dataset(), "nodes")
        with patch("workbench.importing.MAX_EDGES", 1):
            self.assert_failure(lambda: self.dataset(), "edges")

    def test_parquet_metadata_limit_checked_before_loading(self):
        path = self.file(extension=".parquet")
        with patch("workbench.importing.MAX_ROWS", 2):
            self.assert_failure(lambda: inspect_file(path, path.name))

    def test_dictionary_encoded_parquet_cannot_bypass_decoded_size_limit(self):
        import pyarrow.parquet as pq

        path = self.directory / "dictionary.parquet"
        pd.DataFrame({"payload": ["x" * 1024] * 32}).to_parquet(path, index=False)
        with pq.ParquetFile(path) as parquet:
            self.assertLess(parquet.metadata.row_group(0).total_byte_size, 4096)
        with patch("workbench.importing.MAX_UNPACKED_BYTES", 4096):
            error = self.assert_failure(lambda: inspect_file(path, path.name))
        self.assertIn("декодированной таблицы", str(error))
        # The failed decoder must release its descriptor on Windows, too.
        path.rename(self.directory / "released.parquet")

    def test_empty_malformed_and_duplicate_headers(self):
        for contents in ("", "src,dst,amount\n", "src,src,amount\na,b,1\n",
                         "src,dst,amount\na,b,1,extra\n", 'src,dst,amount\n"unterminated,b,1\n'):
            path = self.directory / "bad.csv"
            path.write_text(contents, encoding="utf-8")
            self.assert_failure(lambda: inspect_file(path, path.name))

    def test_json_nested_duplicate_keys_nonfinite_and_bad_shape(self):
        for content in ('[{"a":{"nested":1}}]', '[{"a":1,"a":2}]', '[{"a":NaN}]',
                        '[{"a":Infinity}]', '{}', '[]', '[1]', '{bad'):
            path = self.directory / "bad.json"
            path.write_text(content, encoding="utf-8")
            self.assert_failure(lambda: inspect_file(path, path.name))

    def test_xlsx_sheet_selection_and_missing_sheet(self):
        path = self.file(extension=".xlsx")
        preview = inspect_file(path, path.name, {"sheet": "Переводы"})
        self.assertEqual(preview["sheets"], ["Переводы"])
        self.assertEqual(preview["rows"], 3)
        self.assert_failure(lambda: inspect_file(path, path.name, {"sheet": "missing"}))

    def test_xlsx_formulas_and_unsafe_numeric_identifiers_rejected(self):
        for value in ('=HYPERLINK("https://example.test")', 12345678901234567, 123456789012345.6):
            self.assert_failure(lambda: self.dataset([{**self.rows[0], "payer": value}], extension=".xlsx"))

    def test_xlsx_zip_limits_and_entities_rejected(self):
        path = self.file(extension=".xlsx")
        with patch("workbench.importing.MAX_UNPACKED_BYTES", 10):
            self.assert_failure(lambda: inspect_file(path, path.name))
        with patch("workbench.importing.MAX_ARCHIVE_MEMBERS", 1):
            self.assert_failure(lambda: inspect_file(path, path.name))
        hostile = self.directory / "entities.xlsx"
        with zipfile.ZipFile(hostile, "w") as archive:
            archive.writestr("test.xml", '<!DOCTYPE x [<!ENTITY y "secret">]><x>&y;</x>')
        self.assert_failure(lambda: inspect_file(hostile, hostile.name))

    def test_unsupported_or_disguised_formats_rejected(self):
        path = self.file()
        for filename in ("data.xls", "data.xlsm", "data.zip", "run.py", "data.csv.exe"):
            self.assert_failure(lambda: inspect_file(path, filename))
        self.assert_failure(lambda: inspect_file(path, "data.xlsx"))
        self.assert_failure(lambda: inspect_file(path, "data.parquet"))
        invalid_book = self.directory / "not-a-workbook.xlsx"
        with zipfile.ZipFile(invalid_book, "w") as archive:
            archive.writestr("ordinary.txt", "not a workbook")
        self.assert_failure(lambda: inspect_file(invalid_book, invalid_book.name))
        # On Windows an unclosed constructor stream prevents this rename.
        invalid_book.rename(self.directory / "renamed.xlsx")

    def test_issues_do_not_echo_sensitive_values(self):
        secret = "private-source-value-in-invalid-amount"
        error = self.assert_failure(lambda: self.dataset([{**self.rows[0], "value": secret}]))
        self.assertNotIn(secret, str(error))
        self.assertNotIn(secret, json.dumps(error.issues))
        self.assertEqual(error.issues[0]["row"], 1)
        self.assertEqual(error.issues[0]["field"], "amount")


if __name__ == "__main__":
    unittest.main()
