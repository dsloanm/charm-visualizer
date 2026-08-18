#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
charm-visualizer
================

Visualize Juju charms and their interfaces/integrations as an interactive,
zoomable HTML graph.

Given the path to a charm directory (one containing a ``metadata.yaml`` or
``charmcraft.yaml``), this tool inspects the charm's metadata (the
``requires`` / ``provides`` / ``peers`` sections) and emits a single
self-contained ``.html`` file showing each charm, the interfaces it exposes
or consumes, and the integrations between charms that share a Juju interface.

Hook scripts and event/relation handlers are intentionally out of scope —
this tool focuses purely on charms and their integrations.

Usage::

    python3 visualize_charm.py <charm_dir> [-o output.html]
    python3 visualize_charm.py --all <dir_with_charms> [-o output.html]
    python3 visualize_charm.py <charm_dir> --format dot -o graph.dot
    python3 visualize_charm.py <charm_dir> --format mermaid -o graph.mmd
    python3 visualize_charm.py <charm_dir> --format svg -o graph.svg
    python3 visualize_charm.py <charm_dir> --format json -o graph.json

Run ``python3 visualize_charm.py --help`` for full options.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import textwrap
from pathlib import Path
from string import Template

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "PyYAML is required. Install it with:  apt-get install python3-yaml\n"
    )
    sys.exit(1)

__version__ = "2.0.0"

# Path to the vendored D3 library, shipped alongside this script.
VENDOR_D3 = Path(__file__).resolve().parent / "vendor" / "d3.v7.min.js"
D3_CDN_URL = "https://d3js.org/d3.v7.min.js"


# ---------------------------------------------------------------------------
# Charm inspection
# ---------------------------------------------------------------------------

class CharmInspectionError(Exception):
    """Raised when a charm cannot be inspected."""


class CharmModel(dict):
    """A simple dict-like container for a charm's inspected model.

    Keys: name, summary, description, relations, meta_path, stats.
    """


def _read_metadata(charm_dir: Path) -> dict:
    """Read charm metadata.

    Modern charmcraft-packed charms embed metadata (name, summary,
    description, requires/provides/peers) directly in ``charmcraft.yaml``
    rather than a standalone ``metadata.yaml``.  We prefer ``metadata.yaml``
    when present and fall back to ``charmcraft.yaml``.
    """
    for cand in ("metadata.yaml", "metadata.yml"):
        path = charm_dir / cand
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                raise CharmInspectionError(
                    f"Failed to parse {path}: {exc}"
                ) from exc
            if isinstance(data, dict):
                data["_meta_path"] = str(path)
                data["_meta_source"] = "metadata.yaml"
                return data
    # Fall back to charmcraft.yaml, which may carry the same metadata keys.
    for cand in ("charmcraft.yaml", "charmcraft.yml"):
        path = charm_dir / cand
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
            except yaml.YAMLError as exc:
                raise CharmInspectionError(
                    f"Failed to parse {path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                continue
            # charmcraft.yaml always has a `name`; treat it as metadata source.
            if "name" in data or "requires" in data or "provides" in data or "peers" in data:
                data["_meta_path"] = str(path)
                data["_meta_source"] = "charmcraft.yaml"
                return data
    raise CharmInspectionError(
        f"No metadata.yaml or charmcraft.yaml found in {charm_dir}"
    )


def _build_relations(metadata: dict) -> list[dict]:
    """Normalise the requires/provides/peers sections of metadata.yaml."""
    relations: list[dict] = []
    for role in ("requires", "provides", "peers"):
        section = metadata.get(role) or {}
        if not isinstance(section, dict):
            continue
        for ep_name, spec in section.items():
            if not isinstance(spec, dict):
                spec = {}
            relations.append(
                {
                    "endpoint": ep_name,
                    "role": role,  # requires / provides / peers
                    "interface": spec.get("interface", "—"),
                    "limit": spec.get("limit"),
                    "scope": spec.get("scope"),
                    "optional": bool(spec.get("optional", False)),
                }
            )
    return relations


def inspect_charm(charm_dir: Path) -> CharmModel:
    """Inspect a charm directory and return its model (metadata + relations)."""
    charm_dir = charm_dir.resolve()
    if not charm_dir.is_dir():
        raise CharmInspectionError(f"Not a directory: {charm_dir}")
    metadata = _read_metadata(charm_dir)
    name = metadata.get("name") or charm_dir.name
    summary = (metadata.get("summary") or "").strip()
    description = (metadata.get("description") or "").strip()
    subordinate = bool(metadata.get("subordinate", False))
    relations = _build_relations(metadata)

    model = CharmModel(
        name=name,
        summary=summary,
        description=description,
        subordinate=subordinate,
        meta_path=metadata.pop("_meta_path", None),
        relations=relations,
        stats={
            "relations": len(relations),
            "subordinate": subordinate,
        },
    )
    return model


def is_charm_dir(path: Path) -> bool:
    try:
        _read_metadata(path)
        return True
    except CharmInspectionError:
        return False


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(model: CharmModel) -> dict:
    """Build the graph (nodes + links) consumed by the D3 visualiser.

    Node types:
        charm    - the charm itself (one per charm)
        relation - a requires/provides/peers endpoint
    Link kinds:
        relation    - charm <-> relation
        integration - relation <-> relation across charms sharing an interface
    """
    nodes: list[dict] = []
    links: list[dict] = []

    # Charm node
    nodes.append(
        {
            "id": "charm",
            "type": "charm",
            "charm_index": 0,
            "name": model["name"],
            "summary": model["summary"],
            "description": model["description"],
            "subordinate": model.get("subordinate", False),
            "relations": model["relations"],
            "stats": model["stats"],
        }
    )

    # Relation nodes
    for rel in model["relations"]:
        rid = f"relation:{rel['role']}:{rel['endpoint']}"
        nodes.append(
            {
                "id": rid,
                "type": "relation",
                "charm_index": 0,
                "name": rel["endpoint"],
                "role": rel["role"],
                "interface": rel["interface"],
                "limit": rel["limit"],
                "scope": rel["scope"],
                "optional": rel["optional"],
            }
        )
        links.append({"source": "charm", "target": rid, "kind": "relation", "role": rel["role"], "scope": rel.get("scope")})

    return {"nodes": nodes, "links": links, "charm": model, "charms": [model]}


def build_combined_graph(models: list[CharmModel]) -> dict:
    """Combine multiple charms into one graph (each charm its own cluster).

    Cross-charm ``integration`` links connect relation nodes across charms
    whose interfaces match with complementary roles (requires <-> provides),
    mirroring how Juju connects charms over a shared interface.
    """
    all_nodes: list[dict] = []
    all_links: list[dict] = []
    for i, m in enumerate(models):
        g = build_graph(m)
        # prefix ids with index to avoid collisions
        prefix = f"c{i}:"
        idmap: dict[str, str] = {}
        for n in g["nodes"]:
            nid = prefix + n["id"]
            idmap[n["id"]] = nid
            n2 = dict(n)
            n2["id"] = nid
            n2["charm_index"] = i
            all_nodes.append(n2)
        for l in g["links"]:
            all_links.append(
                {
                    "source": idmap[l["source"]],
                    "target": idmap[l["target"]],
                    "kind": l["kind"],
                    "role": l.get("role"),
                    "scope": l.get("scope"),
                }
            )
        # offset cluster in x so they don't fully overlap
        offset = (i - (len(models) - 1) / 2) * 600
        for n in all_nodes[-len(g["nodes"]):]:
            n["x"] = 400 + offset
            n["y"] = 300

    # Cross-charm integrations: requires <-> provides on a shared interface.
    for i, mi in enumerate(models):
        for j, mj in enumerate(models):
            if i >= j:
                continue
            for ri in mi["relations"]:
                for rj in mj["relations"]:
                    if ri["interface"] in (None, "—") or ri["interface"] != rj["interface"]:
                        continue
                    if sorted([ri["role"], rj["role"]]) == ["provides", "requires"]:
                        a = f"c{i}:relation:{ri['role']}:{ri['endpoint']}"
                        b = f"c{j}:relation:{rj['role']}:{rj['endpoint']}"
                        all_links.append(
                            {
                                "source": a,
                                "target": b,
                                "kind": "integration",
                                "interface": ri["interface"],
                            }
                        )
    return {
        "nodes": all_nodes,
        "links": all_links,
        "charm": models[0] if models else None,
        "charms": models,
    }


# ---------------------------------------------------------------------------
# Lint / diagnostics
# ---------------------------------------------------------------------------

class LintWarning(dict):
    """A single diagnostic warning. Keys: kind, charm, endpoint, message."""


def lint_charms(models: list[CharmModel]) -> list[LintWarning]:
    """Analyse charm models for potential integration issues.

    Returns a list of ``LintWarning`` dicts. Each has:
        kind    — "orphan", "duplicate-provider", or "limit-exceeded"
        charm   — charm name (or None for cross-charm warnings)
        endpoint — endpoint name (or None)
        message — human-readable description

    Checks performed:
        orphan             — a requires/provides endpoint with no matching
                             counterpart (no other charm has the same
                             interface with complementary role).
        duplicate-provider — a single charm with multiple provides endpoints
                             on the same interface.
        limit-exceeded     — a provides endpoint with ``limit: N`` where
                             more than N charms require that interface.
    """
    warnings: list[LintWarning] = []

    requires_by_iface: dict[str, list[tuple[str, dict]]] = {}
    provides_by_iface: dict[str, list[tuple[str, dict]]] = {}
    for model in models:
        for rel in model["relations"]:
            iface = rel["interface"]
            if iface in (None, "—"):
                continue
            if rel["role"] == "requires":
                requires_by_iface.setdefault(iface, []).append((model["name"], rel))
            elif rel["role"] == "provides":
                provides_by_iface.setdefault(iface, []).append((model["name"], rel))

    all_ifaces = set(requires_by_iface) | set(provides_by_iface)
    for iface in sorted(all_ifaces):
        reqs = requires_by_iface.get(iface, [])
        provs = provides_by_iface.get(iface, [])

        # Orphan endpoints: requires with no provides, and vice versa.
        if not provs:
            for charm_name, rel in reqs:
                warnings.append(LintWarning(
                    kind="orphan",
                    charm=charm_name,
                    endpoint=rel["endpoint"],
                    message=f"{charm_name}:{rel['endpoint']} requires interface "
                            f"'{iface}' but no charm provides it",
                ))
        if not reqs:
            for charm_name, rel in provs:
                warnings.append(LintWarning(
                    kind="orphan",
                    charm=charm_name,
                    endpoint=rel["endpoint"],
                    message=f"{charm_name}:{rel['endpoint']} provides interface "
                            f"'{iface}' but no charm requires it",
                ))

        # Duplicate providers: a single charm with multiple provides on the
        # same interface.
        prov_charm_counts: dict[str, list[dict]] = {}
        for charm_name, rel in provs:
            prov_charm_counts.setdefault(charm_name, []).append(rel)
        for charm_name, rels in prov_charm_counts.items():
            if len(rels) > 1:
                endpoints = ", ".join(r["endpoint"] for r in rels)
                warnings.append(LintWarning(
                    kind="duplicate-provider",
                    charm=charm_name,
                    endpoint=endpoints,
                    message=f"{charm_name} provides interface '{iface}' on multiple "
                            f"endpoints: {endpoints}",
                ))

        # Limit over-subscription: a provides endpoint with limit:N where
        # more than N charms require that interface.
        for charm_name, rel in provs:
            limit = rel.get("limit")
            if limit is None or not isinstance(limit, int):
                continue
            num_requirers = len({c for c, _ in reqs})
            if num_requirers > limit:
                warnings.append(LintWarning(
                    kind="limit-exceeded",
                    charm=charm_name,
                    endpoint=rel["endpoint"],
                    message=f"{charm_name}:{rel['endpoint']} provides interface '{iface}' "
                            f"with limit {limit} but {num_requirers} charm(s) require it",
                ))

    return warnings


def format_lint_warnings(warnings: list[LintWarning]) -> str:
    """Format lint warnings as a human-readable table for stderr/stdout."""
    if not warnings:
        return "No issues found.\n"
    lines: list[str] = []
    kind_label = {
        "orphan": "ORPHAN",
        "duplicate-provider": "DUPLICATE",
        "limit-exceeded": "LIMIT",
    }
    for w in warnings:
        label = kind_label.get(w["kind"], w["kind"].upper())
        lines.append(f"  [{label}] {w['message']}")
    lines.append(f"\n{len(warnings)} warning(s) found.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _load_d3() -> str:
    """Return JavaScript source for D3, inlined if available, else a CDN tag."""
    if VENDOR_D3.is_file():
        try:
            return VENDOR_D3.read_text(encoding="utf-8")
        except OSError:
            pass
    return f'<script src="{D3_CDN_URL}"></script>'


def _render_html(graph: dict, title: str, d3_inline: bool = True) -> str:
    # Escape </script> (and </style>) so charm metadata containing those
    # sequences can't break out of the inline <script> tag. The JSON spec
    # allows solidus escaping, so <\/script> is valid JSON + safe in JS.
    data_json = json.dumps(graph).replace("</", "<\\/")
    d3_src = _load_d3()
    d3_tag_is_src = d3_src.lstrip().startswith("<script")
    if d3_inline and not d3_tag_is_src:
        d3_block = f"<script>{d3_src}</script>"
    else:
        d3_block = d3_src

    return _HTML_TEMPLATE.substitute(
        title=html.escape(title),
        data_json=data_json,
        d3_block=d3_block,
    )


_HTML_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>${title} - charm visualizer</title>
<style>
  :root {
    --bg: #0d1b2a;
    --bg2: #142436;
    --panel: #1b2d44;
    --panel-border: #2a4365;
    --text: #e7eef7;
    --text-dim: #9bb0c7;
    --accent: #4cc9f0;
    --requires: #f72585;
    --provides: #06d6a0;
    --peers: #ffd166;
    --integration: #ffd166;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; padding: 0; }
  body {
    background: radial-gradient(circle at 30% 20%, #1a3350 0%, var(--bg) 60%);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    overflow: hidden;
  }
  #app { display: flex; height: 100vh; width: 100vw; }
  #graph { flex: 1; position: relative; cursor: grab; }
  #graph.dragging { cursor: grabbing; }
  svg.graph { width: 100%; height: 100%; display: block; }

  /* Legend / controls */
  .topbar {
    position: absolute; top: 12px; left: 12px; z-index: 10;
    display: flex; flex-direction: column; gap: 8px;
    pointer-events: none;
    max-height: calc(100vh - 24px);
  }
  .title-card {
    background: rgba(20,36,54,0.85); backdrop-filter: blur(8px);
    border: 1px solid var(--panel-border); border-radius: 10px;
    padding: 10px 14px; box-shadow: 0 6px 24px rgba(0,0,0,0.35);
    pointer-events: auto; max-width: 320px;
  }
  .title-card h1 { margin: 0 0 4px 0; font-size: 16px; color: var(--accent); }
  .title-card .sub { font-size: 12px; color: var(--text-dim); }
  .controls {
    display: flex; gap: 6px; pointer-events: auto; flex-wrap: wrap;
  }
  .btn {
    background: rgba(20,36,54,0.85); border: 1px solid var(--panel-border);
    color: var(--text); border-radius: 8px; padding: 6px 10px;
    font-size: 12px; cursor: pointer; backdrop-filter: blur(8px);
    transition: background .15s, transform .1s;
  }
  .btn:hover { background: #233a5a; }
  .btn:active { transform: scale(0.96); }

  .card {
    background: rgba(20,36,54,0.85); backdrop-filter: blur(8px);
    border: 1px solid var(--panel-border); border-radius: 10px;
    padding: 10px 12px; font-size: 12px; pointer-events: auto;
    box-shadow: 0 6px 24px rgba(0,0,0,0.35);
  }
  .card h2 {
    margin: 0 0 6px 0; font-size: 12px; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: .7px;
  }
  .legend .row { display: flex; align-items: center; gap: 8px; margin: 3px 0; }
  .legend .sw { width: 12px; height: 12px; border-radius: 3px; }

  /* Charm visibility toggles */
  .charm-toggles { max-height: 40vh; overflow-y: auto; min-width: 180px; }
  .charm-search {
    width: 100%; box-sizing: border-box; margin-bottom: 6px;
    background: rgba(13,27,42,0.6); border: 1px solid var(--panel-border);
    border-radius: 6px; padding: 5px 8px; color: var(--text);
    font-size: 12px; font-family: inherit;
  }
  .charm-search::placeholder { color: var(--text-dim); }
  .charm-search:focus { outline: none; border-color: var(--accent); }
  .toggle-row {
    display: flex; align-items: center; gap: 8px; margin: 4px 0;
    cursor: pointer; user-select: none;
  }
  .toggle-row input { accent-color: var(--accent); cursor: pointer; }
  .toggle-row .tname {
    color: var(--text); font-size: 12px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .toggle-row input:not(:checked) ~ .tname {
    color: var(--text-dim); text-decoration: line-through;
  }

  /* Issues / lint panel */
  .issues-card { max-height: 35vh; overflow-y: auto; min-width: 220px; }
  .issues-card:empty { display: none; }
  .issue-row {
    cursor: pointer; padding: 4px 6px; margin: 3px 0; border-radius: 5px;
    background: rgba(13,27,42,0.5); border: 1px solid transparent;
    font-size: 11px; line-height: 1.4; transition: background .15s, border-color .15s;
  }
  .issue-row:hover { background: rgba(35,58,90,0.7); border-color: var(--panel-border); }
  .issue-row.active { border-color: var(--accent); background: rgba(35,58,90,0.8); }
  .issue-kind {
    display: inline-block; font-size: 9px; font-weight: 700; padding: 1px 5px;
    border-radius: 3px; margin-right: 4px; text-transform: uppercase;
    letter-spacing: .5px; vertical-align: middle;
  }
  .issue-kind.orphan { background: #4a2a3a; color: #f72585; }
  .issue-kind.duplicate-provider { background: #3a3a1a; color: #ffd166; }
  .issue-kind.limit-exceeded { background: #3a2a1a; color: #ff9e3d; }
  .issue-badge {
    display: inline-block; font-size: 10px; font-weight: 700; padding: 1px 6px;
    border-radius: 8px; background: #4a2a3a; color: #f72585; margin-left: 6px;
    vertical-align: middle;
  }

  /* Side panel */
  #panel {
    width: 380px; min-width: 320px; max-width: 460px;
    background: var(--panel); border-left: 1px solid var(--panel-border);
    overflow-y: auto; padding: 18px 20px; box-shadow: -8px 0 30px rgba(0,0,0,0.4);
    transition: transform .25s ease;
  }
  #panel.collapsed { transform: translateX(110%); position: absolute; right: 0; top: 0; height: 100%; }
  #panel h2 { margin: 0 0 4px 0; font-size: 20px; color: var(--accent); }
  #panel .badge {
    display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 10px;
    background: #233a5a; color: var(--text-dim); margin-bottom: 12px; text-transform: uppercase; letter-spacing: .5px;
  }
  #panel h3 { margin: 18px 0 6px 0; font-size: 13px; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: .7px; border-bottom: 1px solid var(--panel-border); padding-bottom: 4px; }
  #panel .desc { white-space: pre-wrap; font-size: 13px; line-height: 1.55; color: var(--text); }
  #panel ul { margin: 6px 0; padding-left: 18px; }
  #panel li { margin: 4px 0; font-size: 13px; line-height: 1.5; }
  #panel .mono { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; }
  #panel .kv { display: flex; gap: 8px; font-size: 12px; color: var(--text-dim); margin: 2px 0;}
  #panel .kv b { color: var(--text); font-weight: 600; min-width: 70px; }
  #panel .stat { display: inline-block; background: #233a5a; border-radius: 8px; padding: 4px 10px; margin: 2px 4px 2px 0; font-size: 12px; }
  #panel .stat b { color: var(--accent); }
  #panel .close {
    position: sticky; top: 0; float: right; cursor: pointer; color: var(--text-dim);
    background: transparent; border: none; font-size: 18px;
  }
  #panel .close:hover { color: var(--text); }

  /* SVG node styles */
  text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; pointer-events: none; user-select: none; }
  .node { cursor: pointer; }
  .node-label { fill: var(--text); font-size: 13px; font-weight: 600; }
  .node-sub { fill: var(--text-dim); font-size: 10px; }
  .link { stroke-opacity: 0.5; fill: none; }
  .link.relation { stroke: #3a5a7a; stroke-width: 2; }
  .link.relation.container { stroke-dasharray: 5 4; }
  .link.integration { stroke: #ffd166; stroke-width: 3; stroke-opacity: 0.7; }
  .subordinate-ring { fill: none; stroke-dasharray: 4 3; }

  /* zoom hint */
  .hint {
    position: absolute; bottom: 12px; left: 12px; z-index: 10;
    background: rgba(20,36,54,0.7); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 6px 10px; font-size: 11px; color: var(--text-dim);
    backdrop-filter: blur(6px);
  }
  .zoom-indicator {
    position: absolute; top: 12px; right: 12px; z-index: 10;
    background: rgba(20,36,54,0.85); border: 1px solid var(--panel-border);
    border-radius: 8px; padding: 4px 10px; font-size: 11px; color: var(--accent);
    backdrop-filter: blur(6px);
  }
</style>
</head>
<body>
<div id="app">
  <div id="graph">
    <svg class="graph"></svg>
    <div class="topbar">
      <div class="title-card">
        <h1 id="tc-title">charm</h1>
        <div class="sub" id="tc-sub">interactive charm graph</div>
      </div>
      <div class="controls">
        <button class="btn" id="btn-zoom-in">＋ Zoom in</button>
        <button class="btn" id="btn-zoom-out">－ Zoom out</button>
        <button class="btn" id="btn-reset">⟳ Reset</button>
        <button class="btn" id="btn-all">⊕ Show all</button>
        <button class="btn" id="btn-hide-unconnected">◑ Show unconnected</button>
        <button class="btn" id="btn-help">? Help</button>
        <button class="btn" id="btn-export-svg">↓ SVG</button>
        <button class="btn" id="btn-export-png">↓ PNG</button>
        <button class="btn" id="btn-export-json">↓ JSON</button>
      </div>
      <div class="card charm-toggles" id="charm-toggles">
        <h2>Charms</h2>
        <input class="charm-search" id="charm-search" type="search" placeholder="Search charms & interfaces…" autocomplete="off"/>
        <div id="charm-toggle-list"></div>
      </div>
      <div class="card issues-card" id="issues-card" style="display:none;">
        <h2>Issues</h2>
        <div id="issues-list"></div>
      </div>
      <div class="card legend">
        <h2>Legend</h2>
        <div class="row"><span class="sw" style="background:var(--accent)"></span> Charm</div>
        <div class="row"><span class="sw" style="background:var(--accent); border: 1.5px dashed var(--accent); background: transparent;"></span> Subordinate charm</div>
        <div class="row"><span class="sw" style="background:var(--requires)"></span> requires (incoming)</div>
        <div class="row"><span class="sw" style="background:var(--provides)"></span> provides (outgoing)</div>
        <div class="row"><span class="sw" style="background:var(--peers)"></span> peers</div>
        <div class="row"><span class="sw" style="background:transparent; border-bottom: 2px dashed #3a5a7a; width: 16px; height: 0;"></span> container-scope relation</div>
        <div class="row"><span class="sw" style="background:var(--integration)"></span> charm integration</div>
      </div>
    </div>
    <div class="zoom-indicator" id="zoom-ind">100%</div>
    <div class="hint">scroll to zoom · drag to pan · click any node for details · toggle charms or hide unconnected relations</div>
  </div>
  <aside id="panel" class="collapsed"></aside>
</div>

${d3_block}
<script>
const GRAPH_DATA = ${data_json};
const CHARMS = GRAPH_DATA.charms || [GRAPH_DATA.charm];

// Charms share a uniform accent colour; relations are coloured by role.
const CHARM_COLOR = "#4cc9f0";
const ROLE_COLORS = { requires: "#f72585", provides: "#06d6a0", peers: "#ffd166" };

const svg = d3.select("svg.graph");
const W = () => svg.node().clientWidth;
const H = () => svg.node().clientHeight;

// defs for arrowheads + glow
const defs = svg.append("defs");
const f = defs.append("filter").attr("id","glow").attr("x","-50%").attr("y","-50%").attr("width","200%").attr("height","200%");
f.append("feGaussianBlur").attr("stdDeviation","3.5").attr("result","blur");
const merge = f.append("feMerge"); merge.append("feMergeNode").attr("in","blur"); merge.append("feMergeNode").attr("in","SourceGraphic");

["relation","integration"].forEach(kind => {
  const m = defs.append("marker").attr("id","arrow-"+kind).attr("viewBox","0 -5 10 10").attr("refX",18).attr("refY",0).attr("markerWidth",7).attr("markerHeight",7).attr("orient","auto");
  m.append("path").attr("d","M0,-5L10,0L0,5").attr("fill", kind==="integration"?"#ffd166":"#3a5a7a");
});

const g = svg.append("g").attr("class","zoom-layer");

const zoom = d3.zoom().scaleExtent([0.15, 6]).on("zoom", (event) => {
  g.attr("transform", event.transform);
  d3.select("#zoom-ind").text(Math.round(event.transform.k*100)+"%");
});
svg.call(zoom).on("dblclick.zoom", null);

const nodes = GRAPH_DATA.nodes;
const links = GRAPH_DATA.links;
const nodeById = new Map(nodes.map(d => [d.id, d]));
links.forEach(l => { l.source = nodeById.get(l.source); l.target = nodeById.get(l.target); });

function radius(d) {
  if (d.type === "charm") return 34;
  if (d.type === "relation") return 18;
  return 12;
}
function color(d) {
  if (d.type === "charm") return CHARM_COLOR;
  if (d.type === "relation") return ROLE_COLORS[d.role] || "#888";
  return "#888";
}

const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(links).id(d => d.id).distance(l => l.kind === "integration" ? 240 : 150).strength(l => l.kind === "integration" ? 0.3 : 0.5))
  .force("charge", d3.forceManyBody().strength(d => d.type === "charm" ? -900 : -400))
  .force("collide", d3.forceCollide().radius(d => radius(d) + 14))
  .force("x", d3.forceX(W()/2).strength(0.04))
  .force("y", d3.forceY(H()/2).strength(0.06));

const link = g.append("g").attr("class","links").attr("stroke-opacity",0.5)
  .selectAll("path").data(links).join("path")
  .attr("class", d => "link " + d.kind + (d.kind === "relation" && d.scope === "container" ? " container" : ""))
  .attr("marker-end", d => "url(#arrow-" + d.kind + ")");

const node = g.append("g").attr("class","nodes").selectAll("g").data(nodes).join("g")
  .attr("class","node").call(d3.drag()
    .on("start", (event, d) => { if (!event.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on("end",   (event, d) => { if (!event.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

// Subtle outer halo for charm (dashed for subordinate charms)
node.filter(d => d.type === "charm").append("circle")
  .attr("r", d => radius(d) + 10).attr("fill","none")
  .attr("stroke", CHARM_COLOR).attr("stroke-opacity", d => d.subordinate ? 0.6 : 0.25)
  .attr("stroke-width", 2)
  .attr("stroke-dasharray", d => d.subordinate ? "4 3" : null)
  .attr("class", d => d.subordinate ? "subordinate-ring" : null);

// main circle: charms are hollow (dark fill, coloured ring); relations are solid
node.append("circle")
  .attr("r", radius)
  .attr("fill", d => d.type === "charm" ? "#0d1b2a" : color(d))
  .attr("stroke", d => d.type === "charm" ? CHARM_COLOR : d3.color(color(d)).darker(0.6))
  .attr("stroke-width", d => d.type === "charm" ? (d.subordinate ? 1.5 : 2.5) : 1.5)
  .attr("stroke-dasharray", d => d.type === "charm" && d.subordinate ? "4 3" : null)
  .attr("filter", d => d.type === "charm" && !d.subordinate ? "url(#glow)" : null);

// Labels: primary
node.append("text")
  .attr("class","node-label")
  .attr("dy", d => d.type === "charm" ? -radius(d)-14 : radius(d)+13)
  .attr("text-anchor","middle")
  .text(d => d.name);

// sublabel for relations (interface)
node.filter(d => d.type === "relation").append("text")
  .attr("class","node-sub").attr("dy", d => radius(d)+26).attr("text-anchor","middle")
  .text(d => "iface: " + d.interface);

// sublabel for charm (node type)
node.filter(d => d.type === "charm").append("text")
  .attr("class","node-sub").attr("dy", -2).attr("text-anchor","middle")
  .text(d => d.subordinate ? "subordinate" : "charm");

sim.on("tick", () => {
  link.attr("d", d => {
    const sx = d.source.x, sy = d.source.y, tx = d.target.x, ty = d.target.y;
    const dx = tx - sx, dy = ty - sy, dr = Math.sqrt(dx*dx+dy*dy) * 1.6;
    return "M" + sx + "," + sy + "A" + dr + "," + dr + " 0 0,1 " + tx + "," + ty;
  });
  node.attr("transform", d => "translate(" + d.x + "," + d.y + ")");
});

// ---- Visibility ----
// Per-charm visibility (toggle one at a time).
const charmVisible = new Map(CHARMS.map((c, i) => [i, true]));
// When enabled, hides requires/provides relations that aren't part of an
// integration (i.e. no other visible charm shares the interface).
let hideUnconnected = true;

// integration links by endpoint node id
const integrationLinksByNode = new Map();
links.forEach(l => {
  if (l.kind === "integration") {
    [l.source.id, l.target.id].forEach(id => {
      if (!integrationLinksByNode.has(id)) integrationLinksByNode.set(id, []);
      integrationLinksByNode.get(id).push(l);
    });
  }
});

function relationConnected(rel) {
  const ils = integrationLinksByNode.get(rel.id) || [];
  // connected if at least one integration link has both charms visible
  return ils.some(l => charmVisible.get(l.source.charm_index) && charmVisible.get(l.target.charm_index));
}

function nodeVisible(d) {
  if (!charmVisible.get(d.charm_index)) return false;
  if (hideUnconnected && d.type === "relation" && (d.role === "requires" || d.role === "provides")) {
    return relationConnected(d);
  }
  return true;
}
function linkVisible(l) { return nodeVisible(l.source) && nodeVisible(l.target); }
function updateVisibility() {
  node.attr("display", d => nodeVisible(d) ? null : "none");
  link.attr("display", l => linkVisible(l) ? null : "none");
}
updateVisibility();

// ---- Click handling ----
node.on("click", (event, d) => {
  event.stopPropagation();
  if (d.type === "charm") showCharmPanel(d);
  else if (d.type === "relation") showRelationPanel(d);
});
svg.on("click", () => { closePanel(); clearLintHighlight(); });

// ---- Side panel ----
const panel = d3.select("#panel");
function closePanel() { panel.classed("collapsed", true); }
function openPanel(html) {
  panel.html(html);
  panel.classed("collapsed", false);
  panel.select(".close").on("click", () => closePanel());
}
function esc(s){ return (s==null?"":String(s)).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function charmForNode(d) { return CHARMS[d.charm_index]; }

function showCharmPanel(d) {
  const c = charmForNode(d);
  let relsHtml = c.relations.map(r =>
    '<li><b>' + esc(r.endpoint) + '</b> <span style="color:'+ROLE_COLORS[r.role]+'">(' + r.role + ')</span> ' +
    '<span class="mono">' + esc(r.interface) + '</span>' +
    (r.scope === "container" ? ' <span class="mono" style="color:#9bb0c7">[container]</span>' : '') +
    (r.limit? ' · limit '+r.limit : '') + '</li>').join("");
  openPanel(
    '<button class="close">✕</button>' +
    '<h2>'+esc(c.name)+'</h2>' +
    (c.subordinate ? '<div class="badge" style="background:#3a2a4a; color:#b39ddb; border:1px dashed #b39ddb;">subordinate</div>' : '') +
    '<div class="stat"><b>'+c.stats.relations+'</b> relations</div>' +
    (c.summary? '<h3>Summary</h3><div class="desc">'+esc(c.summary)+'</div>':'') +
    (c.description? '<h3>Description</h3><div class="desc">'+esc(c.description)+'</div>':'') +
    '<h3>Relations</h3><ul>'+relsHtml+'</ul>'
  );
}
function showRelationPanel(d) {
  const c = charmForNode(d);
  openPanel(
    '<button class="close">✕</button>' +
    '<h2>'+esc(d.name)+'</h2>' +
    '<div class="badge" style="color:'+color(d)+'">'+esc(d.role)+' relation</div>' +
    '<div class="kv"><b>charm</b>'+esc(c.name)+'</div>' +
    '<div class="kv"><b>endpoint</b><span class="mono">'+esc(d.name)+'</span></div>' +
    '<div class="kv"><b>interface</b><span class="mono">'+esc(d.interface)+'</span></div>' +
    (d.limit != null? '<div class="kv"><b>limit</b>'+esc(d.limit)+'</div>':'') +
    (d.scope? '<div class="kv"><b>scope</b>'+esc(d.scope)+'</div>':'') +
    '<div class="kv"><b>optional</b>'+(d.optional?'yes':'no')+'</div>'
  );
}

// ---- Controls ----
d3.select("#btn-zoom-in").on("click", () => svg.transition().duration(250).call(zoom.scaleBy, 1.4));
d3.select("#btn-zoom-out").on("click", () => svg.transition().duration(250).call(zoom.scaleBy, 1/1.4));
d3.select("#btn-reset").on("click", () => {
  svg.transition().duration(500).call(zoom.transform, d3.zoomIdentity);
  sim.alpha(0.8).restart();
});
d3.select("#btn-all").on("click", () => {
  CHARMS.forEach((c, i) => charmVisible.set(i, true));
  syncToggles();
  updateVisibility();
});
d3.select("#btn-hide-unconnected").on("click", () => {
  hideUnconnected = !hideUnconnected;
  d3.select("#btn-hide-unconnected").text(hideUnconnected ? "◑ Show unconnected" : "◐ Hide unconnected");
  updateVisibility();
});
d3.select("#btn-help").on("click", () => {
  openPanel(
    '<button class="close">✕</button>' +
    '<h2>How to use</h2>' +
    '<ul>'+
    '<li><b>Zoom</b>: scroll wheel or the +/- buttons.</li>'+
    '<li><b>Pan</b>: click and drag the background.</li>'+
    '<li><b>Drag nodes</b>: click and drag a node to reposition it.</li>'+
    '<li><b>Inspect</b>: click a charm or relation node to see its details in this panel.</li>'+
    '<li><b>Toggle charms</b>: use the checkboxes in the "Charms" list (top-left) to show or hide each charm one at a time. Hidden charms and their relations/integrations disappear from the graph.</li>'+
    '<li><b>Search</b>: type in the search box to filter the charm list AND highlight matching nodes in the canvas (by charm name, endpoint name, or interface). Non-matching nodes are dimmed.</li>'+
    '<li><b>Hover</b>: hover a node to highlight it and its connected neighbours/edges; everything else dims temporarily.</li>'+
    '<li><b>Show all</b>: re-enable every charm at once.</li>'+
    '<li><b>Hide unconnected</b>: hides requires/provides relations that aren&#39;t part of an integration with another visible charm. Click again to show them.</li>'+
    '<li><b>Colours</b>: charm nodes share a uniform blue ring; relation nodes are coloured by role — pink=requires, green=provides, gold=peers.</li>'+
    '<li><b>Subordinate charms</b>: drawn with a dashed ring and a "subordinate" sub-label; their container-scope relations use a dashed edge.</li>'+
    '<li><b>Integrations</b>: gold links connect relations across charms that share a Juju interface (requires&lt;-&gt;provides).</li>'+
    '<li><b>Issues</b>: the "Issues" panel lists detected integration problems (orphan endpoints, duplicate providers, limit over-subscription). Click a warning to highlight the affected nodes; click the background to clear.</li>'+
    '<li><b>Export</b>: use the ↓ SVG / ↓ PNG / ↓ JSON buttons to download the current graph (respecting charm visibility and hide-unconnected) as a self-contained SVG, PNG, or JSON file.</li>'+
    '</ul>'
  );
});

// ---- Charm visibility toggles ----
const toggleList = d3.select("#charm-toggle-list");
toggleList.selectAll("label").data(CHARMS).join("label")
  .attr("class","toggle-row")
  .each(function(c, i) {
    const row = d3.select(this);
    row.append("input")
      .attr("type","checkbox").property("checked", true)
      .on("change", function() { toggleCharm(i, this.checked); });
    row.append("span").attr("class","tname").text(c.name);
  });

function toggleCharm(i, on) {
  charmVisible.set(i, on);
  updateVisibility();
}
function syncToggles() {
  toggleList.selectAll("input").property("checked", (d, i) => charmVisible.get(i));
}

// ---- Charm search/filter + in-graph highlight ----
const charmSearch = d3.select("#charm-search");

// Build neighbour map for hover highlight
const neighbours = new Map();
links.forEach(l => {
  const s = typeof l.source === "object" ? l.source.id : l.source;
  const t = typeof l.target === "object" ? l.target.id : l.target;
  if (!neighbours.has(s)) neighbours.set(s, new Set());
  if (!neighbours.has(t)) neighbours.set(t, new Set());
  neighbours.get(s).add(t);
  neighbours.get(t).add(s);
});

function nodeMatchesSearch(d, q) {
  if (d.name && d.name.toLowerCase().indexOf(q) !== -1) return true;
  if (d.interface && d.interface.toLowerCase().indexOf(q) !== -1) return true;
  const charmName = CHARMS[d.charm_index] ? CHARMS[d.charm_index].name : "";
  if (charmName && charmName.toLowerCase().indexOf(q) !== -1) return true;
  return false;
}

function defaultLinkOpacity(l) {
  return d3.select(this).classed("integration") ? 0.7 : 0.5;
}

function resetOpacity() {
  node.transition().duration(150).style("opacity", 1);
  link.transition().duration(150).style("opacity", defaultLinkOpacity);
}

function applySearchFilter() {
  if (activeLintWarning) return;
  const q = charmSearch.property("value").trim().toLowerCase();
  if (!q) { resetOpacity(); return; }
  node.transition().duration(150).style("opacity", d => nodeMatchesSearch(d, q) ? 1 : 0.1);
  link.transition().duration(150).style("opacity", function(l) {
    const sm = typeof l.source === "object" ? l.source : nodeById.get(l.source);
    const tm = typeof l.target === "object" ? l.target : nodeById.get(l.target);
    return (nodeMatchesSearch(sm, q) || nodeMatchesSearch(tm, q)) ? 0.6 : 0.03;
  });
}

function filterCharms() {
  const q = charmSearch.property("value").trim().toLowerCase();
  toggleList.selectAll("label").style("display", function(c) {
    if (!q) return null;
    return c.name.toLowerCase().indexOf(q) !== -1 ? null : "none";
  });
  applySearchFilter();
}
charmSearch.on("input", filterCharms);

// ---- Hover highlight ----
function hoverHighlight(d) {
  if (activeLintWarning) return;
  const nSet = neighbours.get(d.id) || new Set();
  nSet.add(d.id);
  node.transition().duration(100).style("opacity", n => nSet.has(n.id) ? 1 : 0.1);
  link.transition().duration(100).style("opacity", function(l) {
    const sid = typeof l.source === "object" ? l.source.id : l.source;
    const tid = typeof l.target === "object" ? l.target.id : l.target;
    return (nSet.has(sid) && nSet.has(tid)) ? 0.8 : 0.03;
  });
}

function hoverRestore() {
  if (activeLintWarning) return;
  applySearchFilter();
}

node.on("mouseenter", (event, d) => hoverHighlight(d))
    .on("mouseleave", () => hoverRestore());

// ---- Title card ----
const integrationCount = links.filter(l => l.kind === "integration").length;
if (CHARMS.length > 1) {
  const relationCount = CHARMS.reduce((t, c) => t + c.stats.relations, 0);
  d3.select("#tc-title").text(CHARMS.length + " charms");
  d3.select("#tc-sub").text(relationCount + " relations · " + integrationCount + " integrations");
} else {
  const c = CHARMS[0];
  d3.select("#tc-title").text(c.name);
  d3.select("#tc-sub").text(c.stats.relations + " relations · " + integrationCount + " integrations");
}

// ---- Lint warnings panel ----
const LINT_WARNINGS = GRAPH_DATA.lint_warnings || [];
const issuesCard = d3.select("#issues-card");
const issuesList = d3.select("#issues-list");
let activeLintWarning = null;

function findNodesForWarning(w) {
  const charmName = w.charm;
  let endpoints = w.endpoint || "";
  endpoints = endpoints.indexOf(",") !== -1
    ? endpoints.split(", ").map(s => s.trim())
    : [endpoints];
  return nodes.filter(n =>
    n.type === "relation" &&
    CHARMS[n.charm_index] && CHARMS[n.charm_index].name === charmName &&
    endpoints.indexOf(n.name) !== -1
  );
}

function clearLintHighlight() {
  if (!activeLintWarning) return;
  activeLintWarning = null;
  applySearchFilter();
  issuesList.selectAll(".issue-row").classed("active", false);
}

function highlightLintWarning(w) {
  if (activeLintWarning === w) { clearLintHighlight(); return; }
  activeLintWarning = w;
  const targets = findNodesForWarning(w);
  const targetIds = new Set(targets.map(n => n.id));
  const targetCharmIdx = new Set(targets.map(n => n.charm_index));
  node.transition().duration(200).style("opacity", d =>
    (targetIds.has(d.id) || (d.type === "charm" && targetCharmIdx.has(d.charm_index))) ? 1 : 0.15);
  link.transition().duration(200).style("opacity", l => {
    const sid = typeof l.source === "object" ? l.source.id : l.source;
    const tid = typeof l.target === "object" ? l.target.id : l.target;
    return (targetIds.has(sid) || targetIds.has(tid)) ? 0.8 : 0.05;
  });
  issuesList.selectAll(".issue-row").classed("active", d => d === w);
}

if (LINT_WARNINGS.length > 0) {
  issuesCard.style("display", null);
  issuesList.selectAll("div")
    .data(LINT_WARNINGS)
    .join("div")
    .attr("class", "issue-row")
    .each(function(w) {
      const row = d3.select(this);
      const kc = w.kind || "";
      row.append("span").attr("class", "issue-kind " + kc).text(w.kind);
      row.append("span").text(w.message);
    })
    .on("click", function(event, w) { event.stopPropagation(); highlightLintWarning(w); });
  d3.select("#tc-title").append("span")
    .attr("class", "issue-badge")
    .text(LINT_WARNINGS.length + " issue(s)");
} else {
  issuesCard.remove();
}

// gentle centering after a moment
setTimeout(() => {
  try {
    const bounds = g.node().getBBox();
    const parent = svg.node().clientWidth, ph = svg.node().clientHeight;
    const scale = Math.min(0.9, 0.9 * Math.min(parent / (bounds.width+200), ph / (bounds.height+200)));
    const x0 = parent/2 - scale * (bounds.x + bounds.width/2);
    const y0 = ph/2 - scale * (bounds.y + bounds.height/2);
    svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity.translate(x0,y0).scale(scale));
  } catch(e) {}
}, 400);

window.addEventListener("resize", () => {
  sim.force("x", d3.forceX(W()/2).strength(0.04));
  sim.force("y", d3.forceY(H()/2).strength(0.06));
  sim.alpha(0.3).restart();
});

// ---- In-browser export (SVG / PNG / JSON) ----
const EXPORT_BG = "#0d1b2a";

function exportFilename(ext) {
  let t = document.title || "charm-graph";
  t = t.replace(/\\s*-\\s*charm visualizer\\s*$$/i, "").trim();
  const base = (t || "charm-graph").replace(/[^a-z0-9_-]+/gi, "_").replace(/^_+|_+$$/g, "").toLowerCase() || "charm-graph";
  return base + "." + ext;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function inlineSvgStyles(srcRoot, dstRoot) {
  // Copy computed presentation styles from the live SVG onto the exported
  // clone as attributes, so the file is fully self-contained (no CSS
  // stylesheet / variables required when rendered standalone or via <img>).
  const props = ["fill","fill-opacity","stroke","stroke-width","stroke-opacity",
                 "font-size","font-weight","font-family","text-anchor"];
  const src = srcRoot.querySelectorAll("*");
  const dst = dstRoot.querySelectorAll("*");
  for (let i = 0; i < src.length && i < dst.length; i++) {
    const cs = window.getComputedStyle(src[i]);
    for (const p of props) {
      const v = cs.getPropertyValue(p);
      if (v) dst[i].setAttribute(p, v);
    }
  }
}

function buildExportSVG() {
  const svgEl = svg.node();
  const clone = svgEl.cloneNode(true);
  // Drop hidden nodes/links so the export matches what's on screen.
  clone.querySelectorAll('[display="none"]').forEach(el => el.remove());
  // Strip the zoom/pan transform so we export raw node coordinates.
  const zl = clone.querySelector(".zoom-layer");
  if (zl) zl.removeAttribute("transform");
  // Bounds of the actual content (ignoring pan/zoom).
  let bbox;
  try { bbox = g.node().getBBox(); } catch(e) { bbox = {x:0,y:0,width:W(),height:H()}; }
  if (!isFinite(bbox.width) || bbox.width <= 0) { bbox = {x:0,y:0,width:W(),height:H()}; }
  const pad = 60;
  const vbX = bbox.x - pad, vbY = bbox.y - pad;
  const vbW = bbox.width + pad*2, vbH = bbox.height + pad*2;
  clone.setAttribute("viewBox", vbX + " " + vbY + " " + vbW + " " + vbH);
  clone.setAttribute("width", vbW);
  clone.setAttribute("height", vbH);
  clone.removeAttribute("style");
  // Background rect so SVG/PNG have the same dark canvas as the app.
  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("x", vbX); bg.setAttribute("y", vbY);
  bg.setAttribute("width", vbW); bg.setAttribute("height", vbH);
  bg.setAttribute("fill", EXPORT_BG);
  clone.insertBefore(bg, clone.firstChild);
  // Inline computed styles onto the clone.
  inlineSvgStyles(svgEl, clone);
  return clone;
}

function exportSVG() {
  const clone = buildExportSVG();
  const xml = '<?xml version="1.0" encoding="UTF-8"?>\\n' + new XMLSerializer().serializeToString(clone);
  downloadBlob(new Blob([xml], {type: "image/svg+xml;charset=utf-8"}), exportFilename("svg"));
}

function exportPNG() {
  const clone = buildExportSVG();
  const xml = new XMLSerializer().serializeToString(clone);
  const svgUrl = URL.createObjectURL(new Blob([xml], {type: "image/svg+xml;charset=utf-8"}));
  const vb = clone.getAttribute("viewBox").split(/\\s+/).map(Number);
  const scale = 2; // retina-quality
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(vb[2] * scale);
    canvas.height = Math.round(vb[3] * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = EXPORT_BG;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    URL.revokeObjectURL(svgUrl);
    canvas.toBlob(blob => {
      if (blob) downloadBlob(blob, exportFilename("png"));
      else alert("PNG export failed: canvas could not be encoded.");
    }, "image/png");
  };
  img.onerror = () => {
    URL.revokeObjectURL(svgUrl);
    alert("PNG export failed: the SVG could not be rasterised in this browser.");
  };
  img.src = svgUrl;
}

function exportJSON() {
  // GRAPH_DATA.links have source/target resolved to node objects; convert
  // back to id strings for a clean, portable JSON file.
  const data = JSON.parse(JSON.stringify(GRAPH_DATA, (key, val) => {
    if ((key === "source" || key === "target") && val && typeof val === "object" && val.id) return val.id;
    return val;
  }));
  downloadBlob(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}), exportFilename("json"));
}

d3.select("#btn-export-svg").on("click", exportSVG);
d3.select("#btn-export-png").on("click", exportPNG);
d3.select("#btn-export-json").on("click", exportJSON);
</script>
</body>
</html>
""")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _find_charm_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for cur, dirs, files in os.walk(root):
        # don't descend into venvs / hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__",)]
        if any(m in files for m in ("metadata.yaml", "metadata.yml", "charmcraft.yaml", "charmcraft.yml")):
            found.append(Path(cur))
            dirs[:] = []  # don't recurse into the charm
    return sorted(found)


# ---------------------------------------------------------------------------
# Other output formats
# ---------------------------------------------------------------------------

# Colour palette shared with the HTML view, reused by the non-HTML formats.
CHARM_COLOR = "#4cc9f0"
ROLE_COLORS = {"requires": "#f72585", "provides": "#06d6a0", "peers": "#ffd166"}
INTEGRATION_COLOR = "#ffd166"
RELATION_LINK_COLOR = "#3a5a7a"
BG_COLOR = "#0d1b2a"
TEXT_COLOR = "#e7eef7"

SUPPORTED_FORMATS = ("html", "json", "dot", "mermaid", "svg")
DEFAULT_OUTPUT_EXT = {
    "html": ".html",
    "json": ".json",
    "dot": ".dot",
    "mermaid": ".mmd",
    "svg": ".svg",
}


def _graph_for_render(models: list[CharmModel]) -> dict:
    """Pick single-charm vs combined graph builder."""
    if len(models) == 1:
        return build_graph(models[0])
    return build_combined_graph(models)


def _node_label(node: dict) -> str:
    """Human-readable label for a graph node."""
    if node["type"] == "charm":
        return node["name"]
    iface = node.get("interface") or ""
    return f"{node['name']}\\n{iface}" if iface else node["name"]


def _dot_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _dot_node_attrs(node: dict) -> str:
    if node["type"] == "charm":
        style = "filled,bold"
        penwidth = 2
        if node.get("subordinate"):
            style = "filled,dashed"
            penwidth = 1.5
        return (
            f'label="{_dot_escape(node["name"])}", '
            f'fillcolor="{CHARM_COLOR}", style="{style}", '
            f'fontcolor="{BG_COLOR}", penwidth={penwidth}'
        )
    role = node.get("role", "")
    color = ROLE_COLORS.get(role, "#888888")
    name = _dot_escape(node["name"])
    iface = node.get("interface") or ""
    label = f"{name}\\n{_dot_escape(iface)}" if iface else name
    return (
        f'label="{label}", '
        f'fillcolor="{color}", style="filled", '
        f'fontcolor="{BG_COLOR}", penwidth=1.5'
    )


def _render_dot(graph: dict, title: str) -> str:
    lines: list[str] = []
    lines.append(f'digraph "{_dot_escape(title)}" {{')
    lines.append(f'  graph [bgcolor="{BG_COLOR}", label="{_dot_escape(title)}", labelloc=t, fontcolor="{TEXT_COLOR}", fontname="Helvetica"];')
    lines.append(f'  node [shape=ellipse, fontname="Helvetica", fontsize=10];')
    lines.append(f'  edge [fontname="Helvetica", fontsize=9];')
    lines.append("")

    for n in graph["nodes"]:
        lines.append(f'  "{_dot_escape(n["id"])}" [{_dot_node_attrs(n)}];')

    lines.append("")
    for l in graph["links"]:
        src = l["source"] if isinstance(l["source"], str) else l["source"]["id"]
        tgt = l["target"] if isinstance(l["target"], str) else l["target"]["id"]
        if l["kind"] == "integration":
            lines.append(
                f'  "{_dot_escape(src)}" -> "{_dot_escape(tgt)}" '
                f'[color="{INTEGRATION_COLOR}", penwidth=2.5, '
                f'arrowhead=open, fontcolor="{INTEGRATION_COLOR}", '
                f'label="{_dot_escape(l.get("interface", ""))}"];'
            )
        else:
            scope = l.get("scope")
            style = "dashed" if scope == "container" else "solid"
            lines.append(
                f'  "{_dot_escape(src)}" -> "{_dot_escape(tgt)}" '
                f'[color="{RELATION_LINK_COLOR}", penwidth=1.5, arrowhead=none, '
                f'style="{style}"];'
            )
    lines.append("}")
    return "\n".join(lines) + "\n"


def _mermaid_id(node_id: str) -> str:
    # Mermaid node IDs must be alphanumeric (with a few extras). Sanitise.
    safe = []
    for ch in node_id:
        if ch.isalnum() or ch in ("_",):
            safe.append(ch)
        else:
            safe.append("_")
    return "".join(safe)


def _mermaid_escape(s: str) -> str:
    """Escape text for use inside a Mermaid quoted label or edge label.

    Mermaid quoted labels use ``"..."`` and support ``<br/>`` for line
    breaks. Inside the quotes, the only character that must be escaped is
    the double quote itself. Edge labels (``---|text|``) are NOT quoted
    by default, so we also strip the pipe character which would otherwise
    terminate the label early. We wrap edge labels in quotes at the call
    site so that ``|`` and other special chars are safe.
    """
    if not s:
        return ""
    return s.replace('"', "#quot;")


def _render_mermaid(graph: dict, title: str) -> str:
    lines: list[str] = []
    lines.append("---")
    lines.append("title: " + _mermaid_escape(title))
    lines.append("---")
    lines.append("flowchart LR")
    lines.append("")

    for n in graph["nodes"]:
        mid = _mermaid_id(n["id"])
        if n["type"] == "charm":
            label = _mermaid_escape(n["name"])
            if n.get("subordinate"):
                # Dashed border via Mermaid "subroutine" shape + subordinate class
                lines.append(f'  {mid}[/"{label}"/]:::subordinate')
            else:
                lines.append(f'  {mid}(("{label}")):::charm')
        else:
            label = _mermaid_escape(_node_label(n).replace("\\n", "<br/>"))
            role = n.get("role", "")
            lines.append(f'  {mid}(["{label}"]):::rel-{role}')

    lines.append("")
    container_link_indices = []
    for li, l in enumerate(graph["links"]):
        src = l["source"] if isinstance(l["source"], str) else l["source"]["id"]
        tgt = l["target"] if isinstance(l["target"], str) else l["target"]["id"]
        src_m = _mermaid_id(src)
        tgt_m = _mermaid_id(tgt)
        if l["kind"] == "integration":
            iface = _mermaid_escape(l.get("interface") or "")
            lines.append(f'  {src_m} ---|"{iface}"| {tgt_m}')
        else:
            scope = l.get("scope")
            if scope == "container":
                lines.append(f'  {src_m} -.->|"container"| {tgt_m}')
                container_link_indices.append(li)
            else:
                lines.append(f'  {src_m} --- {tgt_m}')

    lines.append("")
    lines.append(f'  classDef charm fill:{CHARM_COLOR},stroke:{CHARM_COLOR},color:{BG_COLOR},stroke-width:2px;')
    lines.append(f'  classDef subordinate fill:{CHARM_COLOR},stroke:{CHARM_COLOR},color:{BG_COLOR},stroke-dasharray: 4 3,stroke-width:1.5px;')
    lines.append(f'  classDef rel-requires fill:{ROLE_COLORS["requires"]},stroke:{ROLE_COLORS["requires"]},color:{BG_COLOR};')
    lines.append(f'  classDef rel-provides fill:{ROLE_COLORS["provides"]},stroke:{ROLE_COLORS["provides"]},color:{BG_COLOR};')
    lines.append(f'  classDef rel-peers fill:{ROLE_COLORS["peers"]},stroke:{ROLE_COLORS["peers"]},color:{BG_COLOR};')
    lines.append(f'  linkStyle default stroke:{RELATION_LINK_COLOR},stroke-width:1.5px;')
    for li in container_link_indices:
        lines.append(f'  linkStyle {li} stroke:{RELATION_LINK_COLOR},stroke-width:1.5px,stroke-dasharray: 5 4;')
    return "\n".join(lines) + "\n"


def _render_json(graph: dict, title: str) -> str:
    payload = {"title": title, **graph}
    return json.dumps(payload, indent=2, default=str) + "\n"


def _compute_static_positions(graph: dict) -> dict[str, tuple[float, float]]:
    """Assign deterministic (x, y) coordinates to every node for SVG output.

    For combined graphs, ``build_combined_graph`` already seeds an x-offset per
    cluster; we keep that and spread the relation nodes evenly around the charm
    node in a circle so nothing overlaps.
    """
    pos: dict[str, tuple[float, float]] = {}
    nodes_by_charm: dict[int, list[dict]] = {}
    for n in graph["nodes"]:
        nodes_by_charm.setdefault(n.get("charm_index", 0), []).append(n)

    import math

    canvas_w = 0.0
    for idx, group in nodes_by_charm.items():
        charm_node = next((n for n in group if n["type"] == "charm"), None)
        if charm_node is None:
            continue
        cx = float(charm_node.get("x") or 400)
        cy = float(charm_node.get("y") or 300)
        pos[charm_node["id"]] = (cx, cy)
        rels = [n for n in group if n["type"] != "charm"]
        count = len(rels)
        # radius grows with the number of relations so labels don't collide
        radius = 90.0 + 18.0 * count
        for i, n in enumerate(rels):
            angle = 2 * math.pi * i / count - math.pi / 2
            pos[n["id"]] = (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        canvas_w = max(canvas_w, cx + radius + 200)

    # Fallback for any node without a position (defensive).
    for n in graph["nodes"]:
        if n["id"] not in pos:
            pos[n["id"]] = (400.0, 300.0)
    return pos


def _svg_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_svg(graph: dict, title: str) -> str:
    pos = _compute_static_positions(graph)
    xs = [x for x, _ in pos.values()]
    ys = [y for _, y in pos.values()]
    pad = 80
    min_x = min(xs) - pad
    min_y = min(ys) - pad
    width = max(xs) + pad - min_x
    height = max(ys) + pad - min_y

    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{min_x:.0f} {min_y:.0f} {width:.0f} {height:.0f}" '
        f'font-family="Helvetica, Arial, sans-serif">'
    )
    out.append(f'<rect x="{min_x:.0f}" y="{min_y:.0f}" width="{width:.0f}" height="{height:.0f}" fill="{BG_COLOR}"/>')
    out.append(f'<text x="{min_x + 10:.0f}" y="{min_y + 24:.0f}" fill="{CHARM_COLOR}" font-size="16" font-weight="bold">{_svg_escape(title)}</text>')

    # Arrowhead marker
    out.append(
        '<defs><marker id="arrow" viewBox="0 -5 10 10" refX="8" refY="0" '
        'markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M0,-5L10,0L0,5" fill="#9bb0c7"/></marker></defs>'
    )

    # Links first so nodes overlay them.
    for l in graph["links"]:
        src = l["source"] if isinstance(l["source"], str) else l["source"]["id"]
        tgt = l["target"] if isinstance(l["target"], str) else l["target"]["id"]
        x1, y1 = pos[src]
        x2, y2 = pos[tgt]
        if l["kind"] == "integration":
            stroke = INTEGRATION_COLOR
            width_attr = "3"
            mid = f'{(x1 + x2) / 2:.0f},{(y1 + y2) / 2:.0f}'
            out.append(
                f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                f'stroke="{stroke}" stroke-width="{width_attr}" stroke-opacity="0.8" '
                f'marker-end="url(#arrow)"/>'
            )
            iface = l.get("interface") or ""
            if iface:
                out.append(
                    f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2 - 6:.0f}" '
                    f'text-anchor="middle" fill="{INTEGRATION_COLOR}" '
                    f'font-size="10">{_svg_escape(iface)}</text>'
                )
        else:
            scope = l.get("scope")
            dash = ' stroke-dasharray="5 4"' if scope == "container" else ""
            out.append(
                f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                f'stroke="{RELATION_LINK_COLOR}" stroke-width="1.5" stroke-opacity="0.6"{dash}/>'
            )

    # Nodes.
    for n in graph["nodes"]:
        x, y = pos[n["id"]]
        if n["type"] == "charm":
            r = 34
            sub = n.get("subordinate", False)
            halo_dash = ' stroke-dasharray="4 3"' if sub else ""
            halo_opacity = "0.6" if sub else "0.25"
            halo_width = "1.5" if sub else "2"
            out.append(
                f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r + 8}" '
                f'fill="none" stroke="{CHARM_COLOR}" stroke-opacity="{halo_opacity}" '
                f'stroke-width="{halo_width}"{halo_dash}/>'
            )
            ring_dash = ' stroke-dasharray="4 3"' if sub else ""
            ring_width = "1.5" if sub else "2.5"
            out.append(
                f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{BG_COLOR}" '
                f'stroke="{CHARM_COLOR}" stroke-width="{ring_width}"{ring_dash}/>'
            )
            out.append(
                f'<text x="{x:.0f}" y="{y - r - 14:.0f}" text-anchor="middle" '
                f'fill="{TEXT_COLOR}" font-size="13" font-weight="600">'
                f'{_svg_escape(n["name"])}</text>'
            )
            sub_label = "subordinate" if sub else "charm"
            sub_fill = "#b39ddb" if sub else "#9bb0c7"
            out.append(
                f'<text x="{x:.0f}" y="{y + 4:.0f}" text-anchor="middle" '
                f'fill="{sub_fill}" font-size="10">{sub_label}</text>'
            )
        else:
            r = 18
            color = ROLE_COLORS.get(n.get("role"), "#888888")
            out.append(
                f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{color}" '
                f'stroke="{color}" stroke-width="1.5"/>'
            )
            out.append(
                f'<text x="{x:.0f}" y="{y + r + 13:.0f}" text-anchor="middle" '
                f'fill="{TEXT_COLOR}" font-size="12" font-weight="600">'
                f'{_svg_escape(n["name"])}</text>'
            )
            iface = n.get("interface") or ""
            if iface:
                out.append(
                    f'<text x="{x:.0f}" y="{y + r + 26:.0f}" text-anchor="middle" '
                    f'fill="#9bb0c7" font-size="10">iface: {_svg_escape(iface)}</text>'
                )

    out.append("</svg>")
    return "\n".join(out) + "\n"


def render(models: list[CharmModel], title: str, fmt: str = "html") -> str:
    graph = _graph_for_render(models)
    if fmt == "html":
        graph["lint_warnings"] = lint_charms(models)
        return _render_html(graph, title)
    if fmt == "json":
        return _render_json(graph, title)
    if fmt == "dot":
        return _render_dot(graph, title)
    if fmt == "mermaid":
        return _render_mermaid(graph, title)
    if fmt == "svg":
        return _render_svg(graph, title)
    raise ValueError(f"unsupported format: {fmt!r}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="visualize_charm.py",
        description="Visualize Juju charms and their interfaces/integrations as an interactive HTML graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              python3 visualize_charm.py ./sample-charms/my-wiki-operator
              python3 visualize_charm.py ./sample-charms -o all.html
              python3 visualize_charm.py --all ./sample-charms
              python3 visualize_charm.py --all ./sample-charms --lint --strict
            """
        ),
    )
    p.add_argument("charm_dir", nargs="?", help="Path to a charm directory (one with metadata.yaml or charmcraft.yaml).")
    p.add_argument("--all", dest="all_dir", metavar="DIR", help="Scan DIR for charm directories and render all of them in one graph.")
    p.add_argument("-o", "--output", default=None, help="Output file (default: charm-graph.<ext> matching --format).")
    p.add_argument("--title", default=None, help="Title for the output (default: charm name).")
    p.add_argument("--print-model", action="store_true", help="Print the inspected charm model as JSON and exit (no output file).")
    p.add_argument("--lint", action="store_true", help="Check for integration issues (orphan endpoints, duplicate providers, limit over-subscription) and exit without rendering.")
    p.add_argument("--strict", action="store_true", help="With --lint, exit non-zero if any warnings are found. No effect without --lint.")
    p.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="html",
        help="Output format: html (default, interactive), json (raw graph), "
        "dot (Graphviz), mermaid (Mermaid flowchart), or svg (static graph).",
    )
    args = p.parse_args(argv)

    models: list[CharmModel] = []
    if args.all_dir:
        root = Path(args.all_dir)
        if not root.is_dir():
            p.error(f"--all: not a directory: {root}")
        charm_dirs = _find_charm_dirs(root)
        if not charm_dirs:
            p.error(f"--all: no charm directories (with metadata.yaml or charmcraft.yaml) found under {root}")
        for cd in charm_dirs:
            try:
                models.append(inspect_charm(cd))
            except CharmInspectionError as exc:
                sys.stderr.write(f"warning: skipping {cd}: {exc}\n")
    else:
        if not args.charm_dir:
            p.error("a charm_dir is required (or use --all DIR).")
        cd = Path(args.charm_dir)
        try:
            models.append(inspect_charm(cd))
        except CharmInspectionError as exc:
            p.error(str(exc))

    if not models:
        p.error("no charms could be inspected.")

    if args.print_model:
        if len(models) == 1:
            print(json.dumps(models[0], indent=2, default=str))
        else:
            print(json.dumps(models, indent=2, default=str))
        return 0

    if args.lint:
        warnings = lint_charms(models)
        sys.stdout.write(format_lint_warnings(warnings))
        if args.strict and warnings:
            return 1
        return 0

    title = args.title or (models[0]["name"] if len(models) == 1 else f"{len(models)} charms")
    out_str = render(models, title, fmt=args.format)
    out = Path(args.output) if args.output else Path(f"charm-graph{DEFAULT_OUTPUT_EXT[args.format]}")
    out.write_text(out_str, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    sys.stdout.write(
        f"Wrote {out} ({size_kb:.0f} KB)  ·  {len(models)} charm(s)  ·  format={args.format}  ·  "
        f"open with:  xdg-open {out}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
