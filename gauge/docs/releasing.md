# First-release distribution

Gauge's distribution, import package, and terminal command are named `gauge`.
Python 3.11+ on macOS/Linux is the initial target. The default install includes
OpenAI integration and terminal tooling; pytest integration activates automatically
when pytest is installed. The `test` extra supplies pytest and parallel workers.

Build from the repository root:

```sh
make build
```

Artifacts are created under `dist/`. The source distribution includes shared
Pixel Lab sources; wheels bundle the dashboard and library assets. Building the
wheel from the source distribution does not require this checkout.

For metadata validation and clean-install verification outside the checkout:

```sh
make check-dist
make check-wheel
```

`make wheel` is an alias for `make build`. Targets require `uv`; build tools are
provisioned automatically. Override the artifact directory with
`make check-wheel DIST_DIR=/tmp/gauge-release-dist`. The clean-install check
removes its temporary environment when it finishes.

Verify the complete tests → CLI → browser flow against this installed wheel using
a fixture provider before uploading. Unit, integration, browser, and type checks
must pass. Installation itself performs no network registration or inference.

## Internal release target

Publish to DoorDash Artifactory `pypi-local`:

- Upload: `https://ddartifacts.jfrog.io/artifactory/api/pypi/pypi-local`
- Install index: `https://ddartifacts.jfrog.io/artifactory/api/pypi/pypi-local/simple/`
- Required publishing group: `devs_with_artifactory_python_write`.
  [Internal access example](https://doordash.slack.com/archives/C4G3BTAAF/p1783729586278629).

Keep credentials outside source control and command-line arguments. Use Twine's
`TWINE_USERNAME` / `TWINE_PASSWORD` environment configuration with an Artifactory
identity/token that has deployment permission.

On October 18, 2026, authenticated index access succeeded and the `gauge` package
entry returned 404. The rebuilt wheel and source distribution passed `twine check`.
The first wheel upload returned **403 Forbidden**; neither artifact was published.
Publishing access must be resolved before retrying. The public PyPI name `gauge`
belongs to another project.

With publishing credentials configured:

```sh
uv tool run --from twine twine upload --non-interactive \
  --repository-url https://ddartifacts.jfrog.io/artifactory/api/pypi/pypi-local \
  dist/gauge-0.1.0a1-py3-none-any.whl dist/gauge-0.1.0a1.tar.gz
```

After a successful upload, verify installation from the index in a clean
virtual environment and confirm the bundled dashboard assets. For a standalone
check, explicitly select Artifactory for Gauge so pip cannot resolve the unrelated
public package:

```sh
python -m pip install --no-deps \
  --index-url https://ddartifacts.jfrog.io/artifactory/api/pypi/pypi-local/simple/ \
  'gauge==0.1.0a1'
```

The `--no-deps` check assumes dependencies were installed separately. For ordinary
installation, configure your organization's approved dependency indexes and
Artifactory credentials first.
