"""DATASETS registry — ordered list of datastream dataset classes."""
from .transactions import TransactionsDataset
from .javalogs import JavaLogsDataset
from .metrics import MetricsDataset

DATASETS = [TransactionsDataset, JavaLogsDataset, MetricsDataset]
