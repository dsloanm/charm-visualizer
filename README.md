# charm-visualizer

Visualize Juju charms and their interfaces/integrations as an interactive, zoomable HTML graph.

Given the path to a charm directory (one containing a `metadata.yaml` or `charmcraft.yaml`), this tool reads the charm's metadata (`requires` / `provides` / `peers`) and emits a single self-contained `.html` file showing each charm, the interfaces it exposes or consumes, and the integrations between charms that share a Juju interface.

**This tool was written using z-ai/glm-5.2.**

## Features

- **Interactive force-directed graph** — drag nodes, pan, and zoom with the scroll wheel (or the +/- buttons).
- **Charms and interfaces** — each charm is a central node with its relations around it, color-coded by role: `requires` = pink, `provides` = green, `peers` = gold. Charm nodes share a uniform blue ring (with a "charm" label) so the graph stays calm and readable; the Charms list is a simple checkbox + name per charm.
- **Subordinate charms** — charms with `subordinate: true` in their metadata are drawn with a dashed ring and a "subordinate" sub-label, and their `scope: container` relations use a dashed edge style so they're visually distinct from principal charms.
- **Integrations** — gold links connect relations across charms that share a Juju interface (`requires` <-> `provides`), mirroring how Juju connects charms over a shared interface.
- **Per-charm visibility toggle** — the "Charms" list (top-left) has a checkbox per charm to toggle its visibility on or off one at a time. Hidden charms and their relations/integrations disappear from the graph. "Show all" re-enables every charm.
- **Hide unconnected** — by default, `requires`/`provides` relations that aren't part of an integration with another visible charm (i.e. no other charm shares the interface) are hidden. The "Show unconnected" button reveals them; click again to hide.
- **Click for a summary** — click a charm node to see its summary, description, and relations; click a relation node to see its endpoint, role, interface, limit, scope, and owning charm.
- **Self-contained output** — the HTML file inlines D3.js (vendored locally in `vendor/`) and all data, so it works fully offline and can be shared/emailed as a single file.
- **Multi-charm mode** — point `--all` at a directory tree and every charm found is rendered in one combined graph, each in its own cluster.
- **Multiple output formats** — in addition to the default interactive HTML, the same graph can be emitted as JSON (raw model), DOT (Graphviz), Mermaid (flowchart, renders inline on GitHub), or a static SVG. Use `--format` to pick.
- **In-browser export** — download the current graph (respecting charm visibility and hide-unconnected state) as SVG, PNG, or JSON directly from the HTML view via the ↓ buttons.
- **Charm search/filter** — a search box in the charm toggle list filters charms by name, useful for graphs with many charms.

## Requirements

- Python 3.8+
- PyYAML (`python3-yaml` on Debian/Ubuntu: `sudo apt-get install python3-yaml`)

No other dependencies — D3.js is vendored in `vendor/d3.v7.min.js`.

## Usage

```bash
# Visualize a single charm
python3 visualize_charm.py path/to/my-charm -o graph.html

# Visualize every charm found under a directory tree (e.g. a bundle)
python3 visualize_charm.py --all path/to/charms-dir -o all.html

# Inspect the parsed model without rendering HTML
python3 visualize_charm.py path/to/my-charm --print-model

# Export in other formats
python3 visualize_charm.py path/to/my-charm --format dot     # → charm-graph.dot (Graphviz)
python3 visualize_charm.py path/to/my-charm --format mermaid  # → charm-graph.mmd (renders on GitHub)
python3 visualize_charm.py path/to/my-charm --format svg      # → charm-graph.svg (static graph)
python3 visualize_charm.py path/to/my-charm --format json     # → charm-graph.json (raw graph)

# Open the result
xdg-open graph.html
```

Run `python3 visualize_charm.py --help` for all options.

### What gets parsed

For each charm, `charmcraft.yaml`'s or `metadata.yaml`'s `requires` / `provides` / `peers` sections define the relations (endpoint, role, interface, limit, scope, optional). The `subordinate` flag is also read and used to visually distinguish subordinate charms. Charm integrations are determined by matching interfaces with complementary roles.

## Sample charms

The `sample-charms/` directory contains two groups of real charms for testing — `filesystem-charms/` (ceph-fs, microceph, lustre-server, etc.) and `slurm-charms/` (slurmctld, slurmd, slurmdbd, mysql, etc.):

```bash
python3 visualize_charm.py --all sample-charms -o sample.html && xdg-open sample.html
```

## Output

The default `--format html` (or no `--format`) writes a single HTML file containing:

- An inline copy of D3.js v7
- The charm model embedded as JSON
- A force-directed SVG graph with toggle-visibility and click-to-inspect behaviour

No web server required — just open the file in any modern browser.

Other formats (selected via `--format`) are useful for embedding, version-controlling, or piping through other tooling:

| Format | Extension | Notes |
| --- | --- | --- |
| `html` (default) | `.html` | Interactive, self-contained, offline-capable. |
| `json` | `.json` | The raw graph (`nodes` / `links` / `charms`) plus `title`. |
| `dot` | `.dot` | Graphviz — `dot -Tpng graph.dot -o graph.png`. |
| `mermaid` | `.mmd` | Mermaid flowchart; renders inline on GitHub and in many Markdown viewers. |
| `svg` | `.svg` | Static SVG with a deterministic radial layout (no JS, no layout engine required). |

If `-o` is omitted the output filename defaults to `charm-graph.<ext>` for the chosen format.

## Layout

```
visualize_charm.py     # the tool (single file, stdlib + PyYAML only)
test_visualize_charm.py  # unit tests (stdlib unittest)
vendor/d3.v7.min.js    # vendored D3.js, inlined into the output
sample-charms/         # example charms in each style, for testing
docs/architecture.md   # contributor's overview of the code structure
AGENTS.md              # project-level guidance for opencode agents
```

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text.

The vendored copy of [D3.js](https://d3js.org) in `vendor/d3.v7.min.js` is Copyright 2010-2023 Mike Bostock and is distributed under its own [ISC license](https://github.com/d3/d3/blob/main/LICENSE).
