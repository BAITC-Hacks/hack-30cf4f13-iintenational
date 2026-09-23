"""Convert the existing small synthetic example into every supported format.

This developer utility never changes data/ or user uploads. The normal app
does not invoke it. Ready-to-import files are included in examples/workbench/.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "workbench"
EXTENSIONS = ("csv", "tsv", "xlsx", "json", "jsonl", "parquet")


def source_tables() -> dict[str, list[dict]]:
    """Use the documented example, not a second independent set of inputs."""
    tables = {}
    for name in ("transactions", "edges"):
        with (EXAMPLES / f"{name}.csv").open(encoding="utf-8-sig", newline="") as stream:
            tables[name] = list(csv.DictReader(stream))
    for row in tables["edges"]:
        row["n_tx"] = int(row["n_tx"])
    tables["nodes"] = json.loads((EXAMPLES / "nodes.json").read_text(encoding="utf-8"))
    return tables


def write_table(path: Path, rows: list[dict]) -> None:
    columns = list(rows[0])
    extension = path.suffix
    if extension in {".csv", ".tsv"}:
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t" if extension == ".tsv" else ",")
            writer.writeheader()
            writer.writerows(rows)
    elif extension == ".json":
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif extension == ".jsonl":
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    elif extension == ".parquet":
        # IDs and monetary decimals stay strings; no float/Excel precision loss.
        pd.DataFrame(rows, columns=columns).to_parquet(path, index=False)
    elif extension == ".xlsx":
        workbook = Workbook()
        try:
            workbook.properties.creator = "Synthetic workbench examples"
            workbook.properties.created = datetime(2026, 7, 1)
            workbook.properties.modified = datetime(2026, 7, 1)
            sheet = workbook.active
            sheet.title = "Данные"
            sheet.append(columns)
            for row in rows:
                sheet.append([row[column] for column in columns])
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                sheet.column_dimensions[column[0].column_letter].width = 23
                for cell in column[1:]:
                    if isinstance(cell.value, str) and not cell.value.startswith("="):
                        cell.number_format = "@"
            workbook.save(path)
        finally:
            workbook.close()
    else:
        raise ValueError(f"Unsupported example format: {extension}")


def generate(output_dir: Path, *, overwrite: bool = False) -> list[Path]:
    tables = source_tables()
    pending = [
        (output_dir / "formats" / extension / f"{name}.{extension}", rows)
        for extension in EXTENSIONS for name, rows in tables.items()
    ]
    tx = tables["transactions"]
    negative = [{**tx[0], "amount": "-5.00"}]
    self_transfer = [{**tx[0], "dst": tx[0]["src"]}]
    formula = [{**tx[0], "amount": "=1+1"}]
    currencies = [{**row, "currency": currency} for row, currency in zip(tx, ("USD", "EUR"), strict=True)]
    bad_date = [{**tx[0], "date": "2026-13-01"}]
    missing_sender = [{key: value for key, value in row.items() if key != "src"} for row in tx]
    pending.extend((output_dir / "invalid" / name, rows) for name, rows in (
        ("negative_amount.csv", negative), ("self_transfer.tsv", self_transfer),
        ("formula.xlsx", formula), ("mixed_currencies.json", currencies),
        ("invalid_date.jsonl", bad_date), ("missing_sender.parquet", missing_sender),
    ))
    # Do not overwrite a user's edited examples unless explicitly requested.
    for path, _ in pending:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f"Expected a regular example file: {path}")
        if path.exists() and not overwrite:
            raise FileExistsError(f"Example already exists: {path}; use --overwrite to regenerate")
    for path, rows in pending:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_table(path, rows)
    return [path for path, _ in pending]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create synthetic import examples, without modifying data/ or history")
    parser.add_argument("--output-dir", type=Path, default=EXAMPLES)
    parser.add_argument("--overwrite", action="store_true", help="replace only the 24 generated example files")
    args = parser.parse_args()
    try:
        files = generate(args.output_dir, overwrite=args.overwrite)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Examples not generated: {exc}\n")
    print(f"Created {len(files)} synthetic files: 18 valid + 6 intentionally invalid, in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
