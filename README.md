# olab_code

A monorepo for packages developed, and used, by the lab. Each package under
[`packages/`](packages/) is independently packaged, versioned, and
installable — there is no umbrella runtime distribution. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for release/development conventions
and [`docs/plans/olab_packages_reorg_plan.md`](docs/plans/olab_packages_reorg_plan.md)
for the full design rationale, plus
[`docs/plans/versioning_pypi_plan.md`](docs/plans/versioning_pypi_plan.md)
for the PyPI publishing plan.

## Installing

```bash
pip install olab-<pkg>   # e.g. pip install olab-camera
```

Each package's own README documents its extras (e.g.
`pip install "olab-camera[yolo,websocket]"`). Workspace-internal
dependencies (e.g. `olab-camera` needing `olab-utils`) resolve
automatically from PyPI once published — no separate install step.

| Package | Distribution | Status |
|---|---|---|
| [`olab_camera`](packages/olab_camera/) | `olab-camera` | [on PyPI](https://pypi.org/project/olab-camera/) |
| [`olab_utils`](packages/olab_utils/) | `olab-utils` | [on PyPI](https://pypi.org/project/olab-utils/) |
| [`olab_rf`](packages/olab_rf/) | `olab-rf` | [on PyPI](https://pypi.org/project/olab-rf/) |
| [`olab_voice`](packages/olab_voice/) | `olab-voice` | [on PyPI](https://pypi.org/project/olab-voice/) |
| [`olab_audio`](packages/olab_audio/) | `olab-audio` | [on PyPI](https://pypi.org/project/olab-audio/) |
| [`olab_playground`](packages/olab_playground/) | `olab-playground` | [on PyPI](https://pypi.org/project/olab-playground/) |
