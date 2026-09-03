# StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT PATCH2

This package freezes the ENG2A 100-sample Gretel development-pilot evaluation and provides the one-off UET server runner for three arms: M0 Direct SQL, M1 J-FS, and M2 Frozen A7.

Local reviewer checks:

```bash
python scripts/data/validate_stageeng2a_gretel_external_development_pilot.py --stage-dir StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT
python -m pytest -q tests/test_stageeng2a_gretel_external_development_pilot.py
```

Official server run:

```bash
bash StageENG2A_GRETEL_EXTERNAL_DEVELOPMENT_PILOT/SERVER_RUN_COMMANDS.sh
```

The bundled `mock_dry_run` is a wiring check only. It uses label-side answers and is not a scientific result.
