# ──────────────────────────────────────────────────────────────────────────────
# ES Activity Simulator
# ──────────────────────────────────────────────────────────────────────────────
#
# Targets:
#   make install                     install Python venv + espipe
#   make run                         interactive run (10m, all-safe scenarios)
#   make run DURATION=5m             run for 5 minutes
#   make run PROBLEMS=safe           run all safe scenarios
#   make run DURATION=2m PROBLEMS=mapping_explosion,hotspot,unassigned
#   make teardown                    delete all sample-* indices/templates/ILM
#   make clean                       remove Python virtual environment
#   make clean-espipe                uninstall espipe via cargo
#   make distclean                   clean + clean-espipe
#   make help                        show this message
#
# ──────────────────────────────────────────────────────────────────────────────

PYTHON   ?= python3
VENV     := sample/.venv
VENV_PY  := $(VENV)/bin/python
VENV_PIP := $(VENV)/bin/pip
REQS     := sample/requirements.txt
ENV_FILE ?= sample/.env

# ── Optional run-time flags ───────────────────────────────────────────────────
# Pass any of these on the command line:
#   DURATION=10m   PROBLEMS=safe   SEED=42   VERBOSE=1
DURATION   ?=
PROBLEMS   ?=
SEED       ?=
VERBOSE    ?=
INTERACTIVE ?=

_RUN_FLAGS :=
ifneq ($(DURATION),)
  _RUN_FLAGS += --duration $(DURATION)
endif
ifneq ($(PROBLEMS),)
  _RUN_FLAGS += --problems $(PROBLEMS)
endif
ifneq ($(SEED),)
  _RUN_FLAGS += --seed $(SEED)
endif
ifneq ($(ENV_FILE),)
  _RUN_FLAGS += --env $(ENV_FILE)
endif
ifneq ($(INTERACTIVE),)
  _RUN_FLAGS += --interactive
endif
ifneq ($(VERBOSE),)
  _RUN_FLAGS += -v
endif

# ── Phony targets ─────────────────────────────────────────────────────────────
.PHONY: help install run teardown clean clean-espipe distclean _espipe _check-python

# ── Default ───────────────────────────────────────────────────────────────────
.DEFAULT_GOAL := help

help:
	@echo ""
	@echo "  ES Activity Simulator"
	@echo ""
	@echo "  Targets:"
	@echo "    install          Install Python venv deps + espipe (Rust)"
	@echo "    run              Run the simulator (interactive if no flags given)"
	@echo "    teardown         Delete all sample-* resources from the cluster"
	@echo "    clean            Remove the Python virtual environment"
	@echo "    clean-espipe     Uninstall espipe via cargo"
	@echo "    distclean        clean + clean-espipe"
	@echo ""
	@echo "  Run variables (all optional):"
	@echo "    DURATION=10m       Run duration: 30s / 5m / 2h  (default: interactive prompt)"
	@echo "    PROBLEMS=safe      Scenarios: ids / all / safe / none  (default: interactive)"
	@echo "    SEED=42            Reproducible random seed"
	@echo "    ENV_FILE=path      Path to credentials file  (default: sample/.env)"
	@echo "    INTERACTIVE=1      Force interactive scenario picker"
	@echo "    VERBOSE=1          Enable debug logging"
	@echo ""
	@echo "  Examples:"
	@echo "    make install"
	@echo "    make run"
	@echo "    make run DURATION=5m PROBLEMS=safe"
	@echo "    make run DURATION=2m PROBLEMS=mapping_explosion,hotspot,unassigned"
	@echo "    make run DURATION=10m PROBLEMS=all SEED=99"
	@echo "    make teardown"
	@echo "    make distclean"
	@echo ""

# ── Install ───────────────────────────────────────────────────────────────────

install: _check-python $(VENV_PY) _espipe
	@echo ""
	@echo "  ✓  All dependencies installed."
	@echo "     Run: make run"
	@echo ""

_check-python:
	@if ! command -v $(PYTHON) >/dev/null 2>&1; then \
		echo ""; \
		echo "  Error: '$(PYTHON)' not found."; \
		echo "  Install Python 3.8+ and re-run 'make install'."; \
		echo ""; \
		exit 1; \
	fi

$(VENV_PY): $(REQS)
	@echo "  Creating virtual environment in $(VENV)…"
	$(PYTHON) -m venv $(VENV)
	$(VENV_PIP) install --quiet --upgrade pip
	$(VENV_PIP) install --quiet -r $(REQS)
	@echo "  ✓  Python dependencies installed ($(VENV))"
	@touch $(VENV_PY)

_espipe:
	@if command -v espipe >/dev/null 2>&1; then \
		echo "  ✓  espipe: $$(which espipe)"; \
	elif command -v cargo >/dev/null 2>&1; then \
		echo "  Installing espipe via cargo (one-time, ~1 min)…"; \
		cargo install espipe; \
		echo "  ✓  espipe installed via cargo"; \
	else \
		echo "  ⚠  cargo not found — espipe will fall back to Docker at runtime."; \
		echo "     Install Rust: https://rustup.rs"; \
	fi

# ── Run ───────────────────────────────────────────────────────────────────────

run: $(VENV_PY)
	$(VENV_PY) sample/run.py $(_RUN_FLAGS)

# ── Teardown ──────────────────────────────────────────────────────────────────

teardown: $(VENV_PY)
	$(VENV_PY) sample/run.py --env $(ENV_FILE) --teardown

# ── Clean ─────────────────────────────────────────────────────────────────────

clean:
	@echo "  Removing virtual environment $(VENV)…"
	rm -rf $(VENV)
	@find sample -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "  ✓  Python environment removed."

clean-espipe:
	@if command -v cargo >/dev/null 2>&1 && \
	   cargo install --list 2>/dev/null | grep -q '^espipe'; then \
		echo "  Uninstalling espipe via cargo…"; \
		cargo uninstall espipe; \
		echo "  ✓  espipe removed."; \
	else \
		echo "  espipe not installed via cargo — nothing to remove."; \
	fi

distclean: clean clean-espipe
	@echo "  ✓  Full clean complete."
