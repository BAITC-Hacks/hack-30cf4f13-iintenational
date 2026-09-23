"""Единые параметры и проверяемые пороги решения."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"

EXPECTED_NODES = 2_248
EXPECTED_EDGES = 3_119
EXPECTED_TRANSACTIONS = 4_840
EXPECTED_SEEDS = 81
EXPECTED_TOTAL_KZT = 365_890_012
EXPECTED_DEPTH_COUNTS = {0: 81, 1: 472, 2: 462, 3: 789, 4: 444}

CONSOLIDATOR_MIN_PAYERS = 8
DISTRIBUTOR_MIN_RECIPIENTS = 10
TRANSIT_RATIO_LOW = 0.8
TRANSIT_RATIO_HIGH = 1.2
COORDINATOR_CENTRALITY_PERCENTILE = 0.95
TOP_N = 30

ROLE_BASE_PRIORITY = {
    "coordinator": 1.00,
    "consolidator": 0.82,
    "distributor": 0.78,
    "transit": 0.62,
    "terminal": 0.48,
    "peripheral": 0.18,
}
