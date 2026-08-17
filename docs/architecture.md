# Architecture

A brief overview of how charm-visualizer is structured, to help
contributors find their way around the code.

## At a glance

```
visualize_charm.py     # the entire tool (single file, ~1370 lines)
test_visualize_charm.py  # unit tests (unittest)
vendor/d3.v7.min.js    # vendored D3.js v7, inlined into HTML output
sample-charms/         # real charm directories used by the test suite
docs/                   # this file
```

The tool is intentionally a **single-file Python script** with one
runtime dependency (PyYAML). There is no `pyproject.toml`, no package
layout, and no build step — contributors can edit `visualize_charm.py`
and immediately re-run it.

## Data flow

The pipeline has three stages, each callable on its own:

```
charm directory  ──►  inspect_charm()  ──►  build_graph()  ──►  render()
(metadata.yaml)       (CharmModel)        (graph dict)        (string)
```

1. **Inspection** — `inspect_charm(dir)` reads `metadata.yaml` or
   `charmcraft.yaml` and returns a `CharmModel` (a dict subclass) with
   `name`, `summary`, `description`, `subordinate`, `relations`, and
   `stats`.
2. **Graph construction** — `build_graph(model)` (single charm) or
   `build_combined_graph(models)` (multiple charms) returns a dict with
   `nodes`, `links`, `charms`. Nodes are typed (`charm` or `relation`);
   links are typed (`relation` for charm↔relation, `integration` for
   cross-charm interface matches).
3. **Rendering** — `render(models, title, fmt)` dispatches to a format
   renderer that turns the graph into a string (HTML, JSON, DOT,
   Mermaid, or SVG).

The `main()` function wires these together: it finds charm directories
(either one or via `--all`), inspects each, builds a graph, and writes
the rendered output to a file.

## Structure of visualize_charm.py

The file is organised into clearly delimited sections, each introduced by
a banner comment:

| Section | Lines (approx.) | Key functions |
|---|---|---|
| Imports + constants | 38-65 | `__version__`, `VENDOR_D3`, `D3_CDN_URL` |
| Charm inspection | 68-183 | `CharmInspectionError`, `CharmModel`, `_read_metadata`, `_build_relations`, `inspect_charm`, `is_charm_dir` |
| Graph construction | 185-301 | `build_graph`, `build_combined_graph` |
| HTML rendering | 304-780 | `_load_d3`, `_render_html`, `_HTML_TEMPLATE` |
| CLI helpers | 783-797 | `_find_charm_dirs`, `render` (dispatch), `main` |
| Other output formats | 800-1280 | colour constants, `_render_dot`, `_render_mermaid`, `_render_json`, `_render_svg`, `_compute_static_positions` |

> Line numbers shift as the file grows — search for the banner comments
> (`# ----...`) to find a section.

### The HTML template

`_HTML_TEMPLATE` is a `string.Template` (a large triple-quoted string)
containing the full HTML, CSS, and JavaScript for the interactive view.
It uses three `${...}` placeholders:

- `${title}` — the page title (HTML-escaped)
- `${data_json}` — the graph dict serialised as JSON
- `${d3_block}` — either an inline `<script>` with the vendored D3
  source, or a `<script src="...">` tag pointing at the CDN

**Important:** because the template uses `string.Template`, any literal
`$` in the embedded JavaScript must be written as `$$`, and any literal
`\` in a Python string that should appear as `\` in the output JS (e.g.
regex escapes) must be written as `\\`. See the `exportFilename` regex
for an example of both.

### Output format renderers

Each non-HTML format has a `_render_<fmt>(graph, title)` function. They
all share the same colour palette defined as module-level constants
(`CHARM_COLOR`, `ROLE_COLORS`, `INTEGRATION_COLOR`, etc.) near the top
of the "Other output formats" section. When adding a visual attribute
(e.g. a new node style), update it in **all** renderers plus the HTML
template — there is no shared styling abstraction today.

## Adding a new output format

1. Add the format name to `SUPPORTED_FORMATS` and `DEFAULT_OUTPUT_EXT`
   (near the top of the "Other output formats" section).
2. Write a `_render_<fmt>(graph, title) -> str` function.
3. Add a dispatch branch in `render()`.
4. Add tests in `FormatRenderTests` in `test_visualize_charm.py`.

## Adding a new metadata field

1. Parse it in `inspect_charm()` (or `_read_metadata` / `_build_relations`
   if it lives in a sub-section).
2. Add it to the `CharmModel` constructor call.
3. Surface it on the charm node in `build_graph()`.
4. If it affects rendering, update the HTML template's `showCharmPanel`
   JS and the relevant `_render_*` functions.
5. Add a test in `SubordinateTests` or `CombinedGraphTests`.

## Tests

Tests use the stdlib `unittest` framework (no pytest). Run them with:

```bash
python3 -m unittest test_visualize_charm
```

The test suite has three classes:

- **`CombinedGraphTests`** — builds the combined graph from all sample
  charms; tests node identity, integration links, and HTML rendering
  smoke checks.
- **`FormatRenderTests`** — tests the non-HTML renderers (JSON, DOT,
  Mermaid, SVG) for structural validity and escaping.
- **`SubordinateTests`** — tests subordinate flag parsing and rendering
  across all formats.

Tests use a small `_charm()` helper to build minimal `CharmModel`
objects without touching the filesystem, except `setUpClass` which
inspects the real sample charms under `sample-charms/`.

## Sample charms

`sample-charms/` contains two groups of real charm directories:

- `filesystem-charms/` — ceph-fs, microceph, filesystem-client
  (subordinate), lustre-server, lustre-server-proxy
- `slurm-charms/` — mysql, sackd, slurmctld, slurmd, slurmdbd,
  slurmrestd, smtp-integrator

These are used by the test suite and are useful for manual testing:

```bash
python3 visualize_charm.py --all sample-charms -o /tmp/graph.html && xdg-open /tmp/graph.html
```

## Conventions

- **No external dependencies** beyond PyYAML. New features should work
  with the Python standard library only.
- **No comments in code** unless necessary for clarity (the code aims to
  be self-documenting).
- **Follow existing style** — 4-space indent, double quotes for strings
  in JS, single quotes for strings in Python (mostly).
- **Conventional Commits** — commit messages use `feat:`, `fix:`, etc.
  (see `git log`).
- **Run tests before committing** — `python3 -m unittest
  test_visualize_charm` should pass with no failures or warnings.
