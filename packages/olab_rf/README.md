# olab_rf

`olab_rf` is a local-first Python package for RF-derived telemetry. It
normalizes ADS-B, AIS, synthetic replay tracks, and scanner workflows into
Python APIs with a SQLite history store and an optional local web UI (a
demo/test surface over the Python backend, not a production frontend).

Migrated from `~/Projects/cuas_practice/src/ub_rf` per
[`docs/plans/olab_packages_reorg_plan.md`](../../docs/plans/olab_packages_reorg_plan.md),
Migration sequence step 3. CUAS notebooks, data, tools, and project-specific
planning notes stay in `cuas_practice`, outside this package; `cuas_practice`
becomes a consumer of this package rather than owning the source.

The MVP is receive-only. Do not install Python dependencies into system
Python; use a project virtual environment.

Normal installation (no `olab_code` checkout required):

```bash
python3 -m venv venv
source venv/bin/activate
pip install "olab-rf[web,ais,pyrtlsdr,nats] @ git+https://github.com/optimatorlab/olab_code.git@<tag-or-sha>#subdirectory=packages/olab_rf"
```

Once release wheels exist, prefer pinning the release's exact URL and
SHA-256 hash instead of a git reference.

**Local development**, against an `olab_code` checkout, to run the test
suite or make changes:

```bash
pip install -e "packages/olab_rf[dev,web,ais,pyrtlsdr,nats]"
pytest packages/olab_rf/tests -q
```

System package installation is intentionally not automated. Run
`olab-rf-check` to inspect local tool availability before installing SDR
decoder tools.

Inspect saved SQLite values with:

```bash
olab-rf-history favorites --config olab_rf.yaml
olab-rf-history frequency-scans --config olab_rf.yaml
```

## Further reading

- Python API and validated active-channel scanner workflow: [docs/python_api.md](docs/python_api.md)
- Installation and decoder setup (RTL-SDR, `readsb`, `rtl_ais`, driver
  conflicts): [docs/install.md](docs/install.md)
- SDRTrunk/JMBE operator capability probe: [docs/sdrtrunk_capability_probe.md](docs/sdrtrunk_capability_probe.md)
- Demo web UI boundary: [docs/web_demo.md](docs/web_demo.md)
- Radio voice segment integration: [docs/voice_segment_integration.md](docs/voice_segment_integration.md)

## Examples

Runnable examples live under [`examples/`](examples/): `replay_tracks.py`,
`frequency_scan.py`, `baseline_then_scan.py`, `history.py`, and
`iq_range_scan.py` (exercised directly by
`tests/test_iq_range_scan_example.py`).

## Test Strategy

Most tests are model-free and run unconditionally. Tests requiring an
optional dependency (`pyais` for AIS parsing, `msgpack` for the NATS
transport, `fastapi`/`uvicorn` for the web demo) self-skip via
`pytest.importorskip`/existing guards when that extra isn't installed —
CI's base-only install runs a green, if smaller, subset; install
`[dev,web,ais,pyrtlsdr,nats]` locally for full coverage.
