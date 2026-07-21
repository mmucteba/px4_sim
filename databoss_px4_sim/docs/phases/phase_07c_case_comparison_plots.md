# Phase 7C — Case Comparison Plots

Goal:

Generate visual comparison plots from Phase 7B batch metrics.

Inputs:

- Default/current PX4 failsafe batch metrics
- Delayed-observation batch metrics
- Phase 7B comparison CSV

Outputs:

- Horizontal max error comparison plot
- 3D max error comparison plot
- Horizontal mean error comparison plot
- Markdown report with plot paths

Acceptance:

- Comparison CSV exists.
- Plots are generated.
- Report is written.
- Default-failsafe and delayed-observation behavior are visually comparable.

## Result

Accepted.

Evidence:

- Phase 7B default-failsafe batch was compared against delayed-observation batch.
- Comparison CSV was generated.
- Comparison Markdown report was generated.
- Plot report was generated.
- Horizontal max error plot was generated.
- 3D max error plot was generated.
- Horizontal mean error plot was generated.

Outputs:

- experiments/comparisons/phase7b_default_vs_delayed/comparison.csv
- experiments/comparisons/phase7b_default_vs_delayed/comparison.md
- experiments/comparisons/phase7b_default_vs_delayed/phase7c_plot_report.md
- experiments/comparisons/phase7b_default_vs_delayed/plots/

Conclusion:

Phase 7C is accepted.

DATABOSS can now run rerunnable GNSS-loss matrices and visually compare default-failsafe behavior against delayed-observation drift behavior.
