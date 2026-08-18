# AGENTS.md

Project-level guidance for opencode agents working on charm-visualizer.

## Project

charm-visualizer is a single-file Python CLI tool that visualizes Juju
charms and their interfaces/integrations as an interactive HTML graph
(or exports to JSON / DOT / Mermaid / SVG). It is intentionally
dependency-light: only PyYAML and the Python standard library.

## Commands

```bash
# Run the tool against the sample charms
python3 visualize_charm.py --all sample-charms -o /tmp/graph.html

# Run the test suite (stdlib unittest, no pytest)
python3 -m unittest test_visualize_charm

# Verify no syntax warnings under strict mode
python3 -W error -m unittest test_visualize_charm

# Lint charm set for integration issues (orphan endpoints, duplicate providers, limit over-subscription)
python3 visualize_charm.py --all sample-charms --lint --strict
```

There is no lint command configured yet. There is no build step — the
tool runs directly via `python3 visualize_charm.py`.

## Code conventions

- **No code comments** unless strictly necessary for clarity.
- **No external dependencies** beyond PyYAML; new features should work
  with the standard library only.
- **Single-file architecture** — all tool code lives in
  `visualize_charm.py`; do not split it into a package without explicit
  instruction.
- **Conventional Commits** — commit messages use `feat:`, `fix:`, etc.
- **Run tests before committing** — `python3 -m unittest
  test_visualize_charm` must pass with no failures or warnings.
- See `docs/architecture.md` for a detailed structural overview.

## HTML template caveats

`_HTML_TEMPLATE` is a `string.Template`. When editing the embedded
JavaScript:

- Any literal `$` must be written as `$$` (e.g. regex end-anchors,
  jQuery).
- Any `\` that should appear in the output JS must be written as `\\`
  in the Python string (e.g. `\s` in regexes, `\n` in strings).
- `json.dumps(graph)` embedded in a `<script>` tag must not contain a
  literal `</script>` — escape it if charm metadata could contain it.

## Rendering conventions

There are five output renderers (HTML/JS, DOT, Mermaid, SVG, JSON). When
adding a visual attribute (e.g. a new node style), update **all**
renderers — there is no shared styling abstraction today. Shared colour
constants live near the top of the "Other output formats" section.

## Generated artifacts

`charm-graph.*` and `sample-output-*.html` are generated outputs. Do
not commit them. (They are not yet in `.gitignore` — a welcome fix.)

## Sign-off

Agent-assisted commits should include:

```
Assisted-by: opencode (openrouter/z-ai/glm-5.2)
```

## Suggested additional inclusions

Consider adding the following to this file as the project evolves:

- **Lint/format commands** — once ruff or black is configured, document
  the exact command here so agents run it before committing.
- **CI status** — once a GitHub Actions workflow exists, note the
  Python versions tested and the workflow filename.
- **Release process** — how to bump `__version__` and tag releases
  (currently `2.0.0` at `visualize_charm.py:61`, never exposed via
  `--version`).
- **Packaging plan** — if `pyproject.toml` is added, document the
  console-script entry point name and install command.
- **External dependencies policy** — when (if ever) a new runtime
  dependency is acceptable.
- **Browser testing** — if Playwright or similar is added for testing
  the interactive HTML view, document how to run those tests.
- **Bundle.yaml / Charmhub features** — once bundle support or
  Charmhub fetch mode is implemented, document the flag and data
  sources here.
