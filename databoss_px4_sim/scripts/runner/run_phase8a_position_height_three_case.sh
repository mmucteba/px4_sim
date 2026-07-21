#!/usr/bin/env bash
set -euo pipefail

cd /opt/databoss_px4_sim

if [[ "$(id -un)" != "px4" ]]; then
  exec sudo -H -u px4 /opt/databoss_px4_sim/scripts/runner/run_phase8a_position_height_three_case.sh "$@"
fi

PYTHON=python3

if [[ -x venv/bin/python ]]; then
  PYTHON=venv/bin/python
fi

"${PYTHON}" scripts/runner/run_batch_matrix_pxh.py \
  experiments/configs/mvp/batches/phase8a_position_height_three_case_60s.yaml \
  --continue-on-fail
