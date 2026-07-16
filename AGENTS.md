# Repository Guidelines

## Scope

Keep this project a small standard-library Python launcher with lightweight POSIX shell installation. Avoid adding a package manager or runtime dependency unless the existing fallback cannot support the feature.

## Required checks

Run before committing:

```bash
make check
make doctor
make dry-run
```

`make check` must include the isolated temporary-HOME install/uninstall smoke test. Any launcher behavior change starts with a failing test.

## Installation contract

- Preserve arbitrary Claude/Codex arguments except documented `--mcp-*` controls.
- Never copy MCP credentials or headers into launcher state or process arguments.
- Keep installation idempotent and preserve shell configuration outside the managed marker block.
- Keep uninstall conservative: remove only this launcher's symlink, library directory, and managed shell block. Preserve preferences unless `--purge` is explicit.
- Verify portability from a fresh clone before publishing a release.
