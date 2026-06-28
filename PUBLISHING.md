# Publishing vortis to PyPI

How to cut a release of `vortis` to [PyPI](https://pypi.org/project/vortis/).
Releases are published **from a clean checkout of `master`**, so the uploaded
artifacts exactly match the released source.

## Prerequisites (one-time)

- A [PyPI](https://pypi.org/account/register/) account **and** a
  [TestPyPI](https://test.pypi.org/account/register/) account (separate sites),
  both with email verified and 2FA enabled.
- An **API token** from each, stored in `~/.pypirc` (chmod 600):

  ```ini
  [distutils]
  index-servers =
      pypi
      testpypi

  [pypi]
    username = __token__
    password = pypi-<your real PyPI token>

  [testpypi]
    username = __token__
    password = pypi-<your TestPyPI token>
  ```

  Prefer **project-scoped** tokens (scope: `vortis`) now that the project exists.
  `~/.pypirc` holds secrets — never commit it; rotate a token immediately if it
  leaks.
- Tooling: `pip install -e ".[dev]"` (installs `build` and `twine`).

## Release steps

### 1. Bump the version

PyPI uploads are **immutable** — a version number can never be re-uploaded, even
if deleted. Bump `version` in `pyproject.toml` for every release (semver:
`MAJOR.MINOR.PATCH`). The first release was `0.1.0`; a bugfix is `0.1.1`, etc.
Merge the bump through the normal PR flow so `master` carries it.

### 2. Build from clean master

```bash
git checkout master
git pull origin master
git status                       # MUST be clean — no uncommitted changes

rm -rf dist/ build/ src/*.egg-info
python -m build                  # -> dist/vortis-<version>.tar.gz + .whl
twine check dist/*               # both must say PASSED
```

### 3. Rehearse on TestPyPI

```bash
twine upload --repository testpypi dist/*
```

Verify it installs and runs from the sandbox in a throwaway venv:

```bash
python -m venv /tmp/vt && source /tmp/vt/bin/activate
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ vortis
python -c "from vortis import Store; s=Store(); s.set('k','v'); print(s.get('k'))"
deactivate && rm -rf /tmp/vt
```

### 4. Tag the release in git

```bash
git tag -a v<version> -m "vortis <version>"
git push origin v<version>
```

### 5. Publish to PyPI (irreversible)

```bash
twine upload dist/*
```

Confirm: `pip install vortis` in a clean venv pulls the new version.

## Notes

- `__token__` is the literal username for both indexes; the token is the password.
- TestPyPI and PyPI are independent — a version used on TestPyPI is still free on
  PyPI.
- `dist/`, `build/`, and `*.egg-info/` are git-ignored; never commit build output.
