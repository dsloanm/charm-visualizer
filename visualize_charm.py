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
    relations = _build_relations(metadata)

    model = CharmModel(
        name=name,
        summary=summary,
        description=description,
        meta_path=metadata.pop("_meta_path", None),
        relations=relations,
        stats={"relations": len(relations)},
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
        links.append({"source": "charm", "target": rid, "kind": "relation", "role": rel["role"]})

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
    data_json = json.dumps(graph)
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
  .link.integration { stroke: #ffd166; stroke-width: 3; stroke-opacity: 0.7; }

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
      </div>
      <div class="card charm-toggles" id="charm-toggles">
        <h2>Charms</h2>
        <div id="charm-toggle-list"></div>
      </div>
      <div class="card legend">
        <h2>Legend</h2>
        <div class="row"><span class="sw" style="background:var(--accent)"></span> Charm</div>
        <div class="row"><span class="sw" style="background:var(--requires)"></span> requires (incoming)</div>
        <div class="row"><span class="sw" style="background:var(--provides)"></span> provides (outgoing)</div>
        <div class="row"><span class="sw" style="background:var(--peers)"></span> peers</div>
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
  .attr("class", d => "link " + d.kind)
  .attr("marker-end", d => "url(#arrow-" + d.kind + ")");

const node = g.append("g").attr("class","nodes").selectAll("g").data(nodes).join("g")
  .attr("class","node").call(d3.drag()
    .on("start", (event, d) => { if (!event.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
    .on("end",   (event, d) => { if (!event.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

// Subtle outer halo for charm
node.filter(d => d.type === "charm").append("circle")
  .attr("r", d => radius(d) + 10).attr("fill","none")
  .attr("stroke", CHARM_COLOR).attr("stroke-opacity",0.25).attr("stroke-width",2);

// main circle: charms are hollow (dark fill, coloured ring); relations are solid
node.append("circle")
  .attr("r", radius)
  .attr("fill", d => d.type === "charm" ? "#0d1b2a" : color(d))
  .attr("stroke", d => d.type === "charm" ? CHARM_COLOR : d3.color(color(d)).darker(0.6))
  .attr("stroke-width", d => d.type === "charm" ? 2.5 : 1.5)
  .attr("filter", d => d.type === "charm" ? "url(#glow)" : null);

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
  .text("charm");

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
svg.on("click", () => closePanel());

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
    (r.limit? ' · limit '+r.limit : '') + '</li>').join("");
  openPanel(
    '<button class="close">✕</button>' +
    '<h2>'+esc(c.name)+'</h2>' +
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
    '<li><b>Show all</b>: re-enable every charm at once.</li>'+
    '<li><b>Hide unconnected</b>: hides requires/provides relations that aren&#39;t part of an integration with another visible charm. Click again to show them.</li>'+
    '<li><b>Colours</b>: charm nodes share a uniform blue ring; relation nodes are coloured by role — pink=requires, green=provides, gold=peers.</li>'+
    '<li><b>Integrations</b>: gold links connect relations across charms that share a Juju interface (requires&lt;-&gt;provides).</li>'+
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


def render(models: list[CharmModel], title: str) -> str:
    if len(models) == 1:
        graph = build_graph(models[0])
    else:
        graph = build_combined_graph(models)
    return _render_html(graph, title)


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
            """
        ),
    )
    p.add_argument("charm_dir", nargs="?", help="Path to a charm directory (one with metadata.yaml or charmcraft.yaml).")
    p.add_argument("--all", dest="all_dir", metavar="DIR", help="Scan DIR for charm directories and render all of them in one graph.")
    p.add_argument("-o", "--output", default="charm-graph.html", help="Output HTML file (default: charm-graph.html).")
    p.add_argument("--title", default=None, help="Title for the HTML page (default: charm name).")
    p.add_argument("--print-model", action="store_true", help="Print the inspected charm model as JSON and exit (no HTML).")
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

    title = args.title or (models[0]["name"] if len(models) == 1 else f"{len(models)} charms")
    html_str = render(models, title)
    out = Path(args.output)
    out.write_text(html_str, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    sys.stdout.write(
        f"Wrote {out} ({size_kb:.0f} KB)  ·  {len(models)} charm(s)  ·  "
        f"open with:  xdg-open {out}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
