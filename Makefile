.DEFAULT_GOAL := help

UV ?= uv
DIST_DIR ?= dist
STRESS_OUTPUT_DIR ?= out/stress
AB_MODEL ?= gpt-4.1-nano
STRESS_SOURCE_MODEL ?=
STRESS_MODELS ?= accounts/fireworks/models/qwen3p8-max accounts/fireworks/models/gpt-oss-120b accounts/fireworks/models/deepseek-v4p1-flash

.PHONY: help build wheel check-dist check-wheel stress-small stress-medium stress-large ab-small ab-medium ab-large

help:
	@printf '%s\n' \
	  'make build        Build wheel and source distribution with bundled UI checks' \
	  'make wheel        Alias for build' \
	  'make check-dist   Build and validate distribution metadata' \
	  'make check-wheel  Validate and install the wheel in a temporary environment' \
	  'make stress-small   Live saturation: 20 OpenAI calls, 3 Fireworks candidates' \
	  'make stress-medium  Live saturation: 200 OpenAI calls, 3 Fireworks candidates' \
	  'make stress-large   Live saturation: 1000 OpenAI calls, 3 Fireworks candidates' \
	  'make ab-small       A/B: 20 native + 20 Gauge calls' \
	  'make ab-medium      A/B: 200 native + 200 Gauge calls' \
	  'make ab-large       A/B: 1000 native + 1000 Gauge calls'

stress-small: STRESS_CALLS = 20
stress-small: STRESS_CONCURRENCY = 4
stress-small: STRESS_CAPACITY = 6
stress-medium: STRESS_CALLS = 200
stress-medium: STRESS_CONCURRENCY = 10
stress-medium: STRESS_CAPACITY = 24
stress-large: STRESS_CALLS = 1000
stress-large: STRESS_CONCURRENCY = 20
stress-large: STRESS_CAPACITY = 96

stress-small stress-medium stress-large:
	@test -n "$$FIREWORKS_API_KEY" || { printf '%s\n' 'Set FIREWORKS_API_KEY before starting the stress backend.' >&2; exit 1; }
	$(UV) run --project gauge python tools/live_gauge_saturation.py \
	  --mode saturation --calls $(STRESS_CALLS) --baseline-calls 4 \
	  --concurrency $(STRESS_CONCURRENCY) --comparison-capacity $(STRESS_CAPACITY) \
	  --comparison-max-calls 3000 \
	  $(foreach model,$(STRESS_MODELS),--candidate-model "$(model)") \
	  $(if $(STRESS_SOURCE_MODEL),--source-model "$(STRESS_SOURCE_MODEL)") \
	  --output "$(STRESS_OUTPUT_DIR)/$@-$$(date +%Y%m%d-%H%M%S).json"

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

ab-small: AB_CALLS = 20
ab-small: AB_CONCURRENCY = 4
ab-small: AB_BATCH = 10
ab-medium: AB_CALLS = 200
ab-medium: AB_CONCURRENCY = 10
ab-medium: AB_BATCH = 50
ab-large: AB_CALLS = 1000
ab-large: AB_CONCURRENCY = 20
ab-large: AB_BATCH = 50

ab-small ab-medium ab-large:
	$(UV) run --project gauge python tools/live_gauge_ab.py \
	  --calls-per-arm $(AB_CALLS) --concurrency $(AB_CONCURRENCY) \
	  --batch-size $(AB_BATCH) --model "$(AB_MODEL)" \
	  --output "$(STRESS_OUTPUT_DIR)/$@-$$(date +%Y%m%d-%H%M%S).json"
