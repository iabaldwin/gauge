.DEFAULT_GOAL := help

UV ?= uv
DIST_DIR ?= dist

.PHONY: help build wheel check-dist check-wheel

help:
	@printf '%s\n' \
	  'make build        Build wheel and source distribution with bundled UI checks' \
	  'make wheel        Alias for build' \
	  'make check-dist   Build and validate distribution metadata' \
	  'make check-wheel  Validate and install the wheel in a temporary environment'

build:
	$(UV) run --no-project --with build python tools/build_gauge_release.py --output "$(DIST_DIR)"

wheel: build

check-dist: build
	$(UV) tool run --from twine twine check "$(DIST_DIR)"/gauge-*.whl "$(DIST_DIR)"/gauge-*.tar.gz

check-wheel: check-dist
	@set -eu; \
	set -- "$(abspath $(DIST_DIR))"/gauge-*.whl; \
	if [ "$$#" -ne 1 ]; then printf '%s\n' 'Expected one Gauge wheel; use a fresh DIST_DIR.' >&2; exit 1; fi; \
	wheel="$$1"; \
	check_dir=$$(mktemp -d "$${TMPDIR:-/tmp}/gauge-wheel-check.XXXXXX"); \
	trap 'rm -rf "$$check_dir"' EXIT HUP INT TERM; \
	$(UV) venv "$$check_dir/venv"; \
	$(UV) pip install --python "$$check_dir/venv/bin/python" "$$wheel"; \
	cd "$$check_dir"; \
	"$$check_dir/venv/bin/python" -I -c 'from gauge.openai import OpenAI, AsyncOpenAI; from importlib.metadata import version; print("Verified installed Gauge", version("gauge"))'; \
	env -u PYTHONPATH "$$check_dir/venv/bin/gauge" --help >/dev/null; \
	printf '%s\n' 'Clean wheel installation and CLI checks passed.'
