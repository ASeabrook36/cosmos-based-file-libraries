# Reusable local development environment

Each checkout owns its `.venv` and editable install. The package download cache
can be shared; an installed environment must not be shared across worktrees.
This prevents a branch's tests from silently importing another branch's source.

```sh
sh scripts/dev setup
sh scripts/dev check
```

`setup` creates `.venv`, installs the exact versions in
`requirements-local.lock`, installs this checkout with the `dev`, `demo` and
`comparison` extras, checks dependencies, and primes the `cl100k_base` tokenizer
cache. It is safe to repeat. It does not activate a shell, start services, read
`.env`, authenticate, provision cloud resources or run live evaluation.
An existing symlinked `.venv` is refused. `doctor` verifies both the environment
and the imported source belong to the current checkout.

Prerequisites: Python 3.12+ with `venv`/`pip`, Git and a POSIX shell. The R0 snapshot
was installed and tested using Python 3.14.3 on macOS arm64. Other Python versions
and operating systems remain unvalidated; the project itself still declares
Python 3.12+. Set `FLI_PYTHON` to a Python executable to choose the bootstrap
interpreter. This setting does not replace an already-created `.venv`.

Dependency downloads and the first tokenizer-cache fill require network access.
Use the normal pip cache, or set `PIP_CACHE_DIR` to a writable directory outside
the repository in a restricted session. Setup reports installation failures;
it does not silently borrow another checkout's environment or change versions.
The lock is a version snapshot of the resolved runtime, development, demo and
Responses-comparison dependencies plus the editable build backend. It is not a
cross-platform, hash-verified supply-chain lock. Keep `azure-cosmos` and `openai`
versions matched across experiments, and review the whole snapshot when upgrading.
Optional `reuse-lab` PDF-fixture generation is not part of this setup.

Available actions:

| Command | Scope |
| --- | --- |
| `sh scripts/dev setup` | Install local dependencies and verify source isolation |
| `sh scripts/dev doctor` | Check interpreter, editable source and key package versions |
| `sh scripts/dev test -q` | Full local unit/API suite; accepts normal pytest arguments |
| `sh scripts/dev lint` | Ruff lint and format checks, without modifying files |
| `sh scripts/dev privacy` | Repository reference-privacy check |
| `sh scripts/dev check` | Doctor, tests, lint, format and privacy in sequence |

Pass a private deny-list file with `sh scripts/dev privacy --deny-file "$PRIVATE_DENY_FILE"`
when sanitizing live evidence. Keep it outside the repository.

The checked-in `.codex/environments/environment.toml` runs only `setup` when a
future worktree is created, and exposes Test, Lint and Check actions. This uses the
desktop app's [local environment support](https://learn.chatgpt.com/docs/environments/local-environment).
The commands also work independently of the app. Cloud configuration and
credentials remain separate, explicitly invoked runtime steps.
