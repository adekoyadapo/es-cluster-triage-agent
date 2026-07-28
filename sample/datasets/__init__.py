"""DATASETS registry — imported by run.py and es_admin.py."""
from .transactions import TransactionsDataset
from .javalogs import JavaLogsDataset
from .bulky import BulkyDataset

DATASETS = [TransactionsDataset, JavaLogsDataset, BulkyDataset]
