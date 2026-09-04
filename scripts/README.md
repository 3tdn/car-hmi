# Configuration Generator from DBC

This directory contains utilities used to create/merge static configuration files from DBC files.

Requirements
 - Python 3.8+
 - Install runtime dependencies:

```bash
pip install cantools pyyaml
```

Main files
 - `dbc_utils.py` — common utility functions: parse DBC and read/write JSON.
 - `gen_signals_from_dbc.py` — create/merge `config/signals.json` from DBC.
 - `gen_alarms_from_dbc.py` — create/merge `config/alarms.json` from DBC.
 - `gen_configs_from_dbc.py` — combined script (kept for convenience).
 - `gen_can_json.py` — aggregate DBC messages/signals into `config/can.json`.

Basic usage examples

- Run in dry-run mode (show what will be added, do not write files):

```bash
python scripts/gen_signals_from_dbc.py -d path/to/dbc_or_dir --dry-run
python scripts/gen_alarms_from_dbc.py -d path/to/dbc_or_dir --dry-run
```

- Generate and write to the default configuration paths:

```bash
python scripts/gen_signals_from_dbc.py -d path/to/dbc_dir
python scripts/gen_alarms_from_dbc.py -d path/to/dbc_dir
```

- Specify an output path and allow overwrite:

```bash
python scripts/gen_signals_from_dbc.py -d path/to/file.dbc --out config/signals.json --overwrite
python scripts/gen_alarms_from_dbc.py -d path/to/file.dbc --out config/alarms.json --overwrite
```

Notes
 - The scripts use `cantools` to parse DBC; they extract the `name`, `minimum`, `maximum`, and `unit` attributes (if available).
 - The generators will not delete or change existing entries unless you pass `--overwrite`.
 - The generated YAML uses simple default values:
   - Signals (`signals`): `display_name` = signal name, `group` = `unknown`, `widget` = `gauge`, `writable` = `false`.
   - Alarms (`alarms`): `warning_high`/`warning_low` are taken from DBC `maximum`/`minimum` when available; `critical` thresholds default to `null`.
 - Please review the generated files and adjust thresholds/groups/widgets to fit the project.

Example (Windows PowerShell)

```powershell
python .\scripts\gen_signals_from_dbc.py -d .\db\ -v --dry-run
python .\scripts\gen_alarms_from_dbc.py -d .\db\ --out configlarms.json
```

Advanced options I can help with:
 - Add a `--apply-both` script that runs both generators in one command.
 - Implement smarter threshold heuristics (for example: warning = 75% of max, critical = 95%).
 - Add unit tests for the generator scripts.
