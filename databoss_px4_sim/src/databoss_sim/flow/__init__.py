"""DATABOSS modular optical-flow package.

The contract: capture, bridge, and analysis consume a FlowEstimator by name.
New algorithms are a new module + registry entry; nothing else changes.
"""
from .estimator import FlowEstimator, FlowSample
from .lk_estimator import LucasKanadeFlowEstimator
from .sift_estimator import SiftFlowEstimator

ESTIMATORS = {
    "lk": LucasKanadeFlowEstimator,
    "sift": SiftFlowEstimator,
}


def make_estimator(name: str, **kwargs) -> FlowEstimator:
    try:
        cls = ESTIMATORS[name]
    except KeyError as exc:
        raise ValueError(f"unknown flow estimator: {name!r}; known: {sorted(ESTIMATORS)}") from exc
    return cls(**kwargs)
