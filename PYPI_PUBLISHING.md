# Publishing Pocket Editor on PyPI

PyPI package: `pocket-editor` · version: `2026.11.19.post1` · owner account: `somidouth`.

The project owner has left the license undecided. No license grant is added by this package.

## One-time account setup

Create and verify your PyPI account and enable two-factor authentication. While signed in, open https://pypi.org/manage/account/publishing/ and add a pending GitHub publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `pocket-editor` |
| GitHub owner | `somidouth-collab` |
| Repository name | `pocket-editor` |
| Workflow filename | `publish-pypi.yml` |
| Environment name | `pypi` |

No PyPI password or API token belongs in the repository or chat. This workflow uses PyPI Trusted Publishing through GitHub OIDC. The first successful upload creates the PyPI project in the account that registered the pending publisher; creating a pending publisher does not reserve the package name.

## Build and verify

Install `build` and `twine` in a dedicated virtual environment, then run:

```sh
python -B -m unittest -v test_release test_pypi
python -B tools/build_pypi.py
python -m twine check --strict dist/pypi/*
python -B tools/check_pypi.py dist/pypi
```

The builder stages only curated source files, then uses setuptools to build a wheel and a self-contained source distribution. It bundles plugins, offline help, AI Assistant provider adapters, and optional PHP dependency manifests. Personal settings, session backups, invitations, node_modules, and tests are excluded from the installed application payload. The standalone source distribution can be rebuilt with `python -m build` after extraction.

## Publish from GitHub

Open Actions → Build and publish PyPI package → Run workflow. Leave Publish unchecked for a build-only check. After the pending publisher is configured, run it with Publish checked on the main branch. The `pypi` GitHub environment must exist and match the PyPI configuration. Only the publish job receives permission to request an OIDC token.

PyPI does not permit replacing previously uploaded files. Increment the version in both `packaging/pyproject.toml` and `packaging/pocket_editor_app/__init__.py` for subsequent releases. Update the runtime-path example in `packaging/README.md` as well.
