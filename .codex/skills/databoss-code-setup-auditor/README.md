# DATABOSS Code & Setup Auditor Skill

A ready-to-use project skill for auditing the DATABOSS PX4/Gazebo codebase and experimental setup.

## Contents

- `SKILL.md` — main agent instructions
- `references/current_project_context.md` — project-specific context and known traps
- `templates/audit_report.md` — evidence-first report template
- `scripts/collect_databoss_context.sh` — read-only environment/repository snapshot helper

## Suggested project placement

```text
/opt/databoss_px4_sim/.skills/databoss-code-setup-auditor/
```

or place it in the skill directory used by your coding agent.

## First invocation

```text
Use the DATABOSS Code & Setup Auditor skill. Perform a read-only audit of the current codebase and environment. Trace the selected scenario from YAML through runner code to effective PX4/Gazebo runtime values and latest log evidence. Do not edit code yet.
```
