PYTHON ?= python3
PREFIX ?= $(HOME)/.local
SHELL_RC ?= $(HOME)/.zshrc

.PHONY: check doctor dry-run install uninstall

check:
	$(PYTHON) -m py_compile mcp_launcher.py
	$(PYTHON) -m unittest discover -s tests -v
	sh -n install.sh uninstall.sh tests/test_doctor.sh tests/test_install.sh
	sh tests/test_doctor.sh
	sh tests/test_install.sh

doctor:
	@command -v $(PYTHON) >/dev/null || { echo "missing: $(PYTHON)"; exit 1; }
	@$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
	@has_cli=0; \
	if command -v claude >/dev/null; then \
		echo "claude: $$(command -v claude)"; has_cli=1; \
	else \
		echo "claude: missing (wrapper will activate after installation)"; \
	fi; \
	if command -v codex >/dev/null; then \
		echo "codex: $$(command -v codex)"; has_cli=1; \
	else \
		echo "codex: missing (wrapper will activate after installation)"; \
	fi; \
	[ "$$has_cli" -eq 1 ] || { echo "missing: install Claude Code and/or Codex CLI"; exit 1; }
	@command -v gum >/dev/null && echo "gum: $$(command -v gum) (enhanced picker)" || echo "gum: optional; text picker will be used"

dry-run:
	./install.sh --dry-run --prefix "$(PREFIX)" --shell-rc "$(SHELL_RC)"

install: check
	./install.sh --prefix "$(PREFIX)" --shell-rc "$(SHELL_RC)"

uninstall:
	./uninstall.sh --prefix "$(PREFIX)" --shell-rc "$(SHELL_RC)"
