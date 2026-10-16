# File Explorer

Read-only Herdr plugin for browsing a Git checkout and reviewing diffs. It does not edit source files or change Git state.

## Layout

Install and run use only these paths:

- `herdr-plugin.toml` — plugin id `herdr-explorer`, pane id `explorer`
- `scripts/install.sh` — Herdr runs this on `plugin install`
- `scripts/launch.sh` — starts `.venv/bin/python -m herdr_explorer`
- `herdr_explorer/` — the application

`dev/` holds tests, screenshots, and `dev/bootstrap.sh`. A GitHub install does not use it. `herdr plugin link` does not run the installer, so a local checkout needs `dev/bootstrap.sh` first.

## Commands

Python 3.11 or newer is required. On macOS the system `python3` is often 3.9, so use the project virtualenv.

```bash
dev/bootstrap.sh
.venv/bin/python -m pytest
```

Open the pane with `herdr plugin pane open --plugin herdr-explorer --entrypoint explorer`. The running process does not reload; close the pane and open it again after a behavior change.

## Versions

Keep these three strings identical:

- `herdr_explorer/__version__`
- `version` in `herdr-plugin.toml`
- `version` in `pyproject.toml`

The pane title is `File Explorer` plus that version. Below `1.0.0` is a beta. Bump the patch number for a fix that behaves the same, the minor number for new behavior, and ship `1.0.0` when it leaves beta.

## Rules

- `run_git` may run only `status` and `diff`. Reject `--output` and `--ext-diff`. Inject `--no-ext-diff` on diff.
- Inside Herdr, browse the workspace project. Do not browse the plugin checkout.
- Herdr forwards classic X10 mouse reports. Decode them in `install_cell_mouse`. The enable string is `_CELL_MOUSE`. Never send `1000l`, `1002l`, or `1003l`.
- Decode terminal input with `surrogateescape`. An X10 coordinate byte of 128 or higher must not raise `UnicodeDecodeError`; that closes the pane.
- Diff and file styles must be real Style objects. `None` crashes Textual's monochrome filter.
- Do not edit the user's Herdr config during install. Shortcut instructions belong in the README.
