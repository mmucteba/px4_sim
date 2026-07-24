"""Pydantic contracts for comparison manifest.yaml / summary.json.

Reuses scripts/analysis/comparison_manifest.py's Case/Manifest dataclasses
and load_manifest() for the actual manifest-parsing logic rather than
reimplementing it - this module only adds a Pydantic mirror for clean API
serialization.

summary.json's per-case shape was checked across all 17 real summary.json
files (Phase 17A grounding pass, 2026-07-24): only 8 fields are reliably
common across case entries (key, label, run_dir, kind, gnss_state,
world_variant, short, replicate_of - matching the Case dataclass almost
1:1), because the comparison-report generator's output shape changed
significantly across the project's history (a newer ~99-104-count "rich"
shape with camera_inputs/commands/config/metrics/status sub-objects
coexists with much sparser older shapes down to 2-5 fields). Rather than
force one rigid schema onto genuinely different generator eras, case
entries are typed with the common fields plus a permissive extras dict -
exactly the "thin wrapper, don't reimplement" approach the project's own
plan called for here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from scripts.analysis.comparison_manifest import Manifest, load_manifest


class ComparisonCase(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    short: str
    kind: str
    gnss_state: str
    world_variant: str
    run_dir: str
    replicate_of: str | None = None


class ComparisonManifestModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    title: str
    cases: list[ComparisonCase]


def load_comparison_manifest(path: Path) -> ComparisonManifestModel:
    """Load a manifest.yaml via the existing comparison_manifest.load_manifest()
    and convert the resulting dataclass into a Pydantic model for API use."""
    manifest: Manifest = load_manifest(path)
    return ComparisonManifestModel(
        name=manifest.name,
        title=manifest.title,
        cases=[
            ComparisonCase(
                key=c.key,
                label=c.label,
                short=c.short,
                kind=c.kind,
                gnss_state=c.gnss_state,
                world_variant=c.world_variant,
                run_dir=str(c.run_dir),
                replicate_of=c.replicate_of,
            )
            for c in manifest.cases
        ],
    )


class ComparisonSummaryCase(BaseModel):
    """One entry of a comparison's summary.json - loosely typed by design,
    see module docstring for why."""

    model_config = ConfigDict(extra="allow")

    key: str | None = None
    label: str | None = None
    short: str | None = None
    kind: str | None = None
    gnss_state: str | None = None
    world_variant: str | None = None
    run_dir: str | None = None
    replicate_of: str | None = None
    metrics: dict[str, Any] | None = None
    status: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


def load_comparison_summary(path: Path) -> list[ComparisonSummaryCase]:
    import json

    with Path(path).open() as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"expected a list of case entries in {path}, got {type(data).__name__}")
    return [ComparisonSummaryCase.model_validate(entry) for entry in data]
