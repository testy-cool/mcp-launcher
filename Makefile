PYTHON ?= python3
PREFIX ?= $(HOME)/.local
SHELL_RC ?= $(HOME)/.zshrc

.PHONY: check doctor dry-run install uninstall

check:
	$(PYTHON) -m py_compile mcp_launcher.py
	$(PYTHON) -m unittest discover -s tests -v
	sh -n install.sh uninstall.sh tests/test_install.sh
	sh tests/test_install.sh

doctor:
	@command -v $(PYTHON) >/dev/null || { echo "missing: $(PYTHON)"; exit 1; }
	@$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
	@command -v claude >/dev/null && echo "claude: $$(command -v claude)" || { echo "missing: claude"; exit 1; }
	@command -v codex >/dev/null && echo "codex: $$(command -v codex)" || { echo "missing: codex"; exit 1; }
	@command -v gum >/dev/null && echo "gum: $$(command -v gum) (enhanced picker)" || echo "gum: optional; text picker will be used"

dry-run:
	./install.sh --dry-run --prefix "$(PREFIX)" --shell-rc "$(SHELL_RC)"

install: check
	./install.sh --prefix "$(PREFIX)" --shell-rc "$(SHELL_RC)"

uninstall:
	./uninstall.sh --prefix "$(PREFIX)" --shell-rc "$(SHELL_RC)"
