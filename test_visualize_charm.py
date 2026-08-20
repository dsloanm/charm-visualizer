import unittest
from pathlib import Path

import visualize_charm as visualizer


SAMPLE_CHARMS = Path(__file__).parent / "sample-charms"


def _charm(name, requires=None, provides=None, peers=None, subordinate=False, scopes=None, limits=None):
    """Build a minimal CharmModel with the given relations.

    ``scopes`` optionally maps endpoint names to a relation scope (e.g.
    ``"container"``); endpoints not listed default to ``None``.

    ``limits`` optionally maps endpoint names to an integer limit.
    """
    scopes = scopes or {}
    limits = limits or {}
    relations = []
    for role, section in (
        ("requires", requires or {}),
        ("provides", provides or {}),
        ("peers", peers or {}),
    ):
        for endpoint, interface in section.items():
            relations.append(
                {
                    "endpoint": endpoint,
                    "role": role,
                    "interface": interface,
                    "limit": limits.get(endpoint),
                    "scope": scopes.get(endpoint),
                    "optional": False,
                }
            )
    return visualizer.CharmModel(
        name=name,
        summary="",
        description="",
        subordinate=subordinate,
        relations=relations,
        stats={"relations": len(relations), "subordinate": subordinate},
    )


class CombinedGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = [
            visualizer.inspect_charm(path)
            for path in visualizer._find_charm_dirs(SAMPLE_CHARMS)
        ]
        cls.graph = visualizer.build_combined_graph(cls.models)

    def test_nodes_retain_their_charm_identity(self):
        charm_nodes = [node for node in self.graph["nodes"] if node["type"] == "charm"]

        self.assertEqual(len(charm_nodes), len(self.models))
        for index, node in enumerate(charm_nodes):
            self.assertEqual(node["charm_index"], index)
            self.assertEqual(node["name"], self.graph["charms"][index]["name"])

    def test_relation_nodes_carry_charm_index(self):
        relation_nodes = [
            node for node in self.graph["nodes"] if node["type"] == "relation"
        ]

        self.assertTrue(relation_nodes)
        per_charm = {i: 0 for i in range(len(self.models))}
        for node in relation_nodes:
            self.assertIn(node["charm_index"], per_charm)
            per_charm[node["charm_index"]] += 1

        for index, model in enumerate(self.models):
            self.assertEqual(per_charm[index], len(model["relations"]))

    def test_no_event_or_handler_nodes_remain(self):
        types = {node["type"] for node in self.graph["nodes"]}

        self.assertEqual(types, {"charm", "relation"})

    def test_integration_links_connect_complementary_roles(self):
        node_by_id = {node["id"]: node for node in self.graph["nodes"]}
        integrations = [
            link for link in self.graph["links"] if link["kind"] == "integration"
        ]

        for link in integrations:
            source = node_by_id[link["source"]]
            target = node_by_id[link["target"]]
            self.assertEqual(
                sorted([source["role"], target["role"]]), ["provides", "requires"]
            )
            self.assertEqual(source["interface"], target["interface"])

    def test_complementary_interfaces_produce_an_integration(self):
        consumer = _charm("app", requires={"db": "mysql"})
        provider = _charm("database", provides={"db": "mysql"})
        graph = visualizer.build_combined_graph([consumer, provider])

        integrations = [
            link for link in graph["links"] if link["kind"] == "integration"
        ]

        self.assertEqual(len(integrations), 1)
        self.assertEqual(integrations[0]["interface"], "mysql")

    def test_rendered_html_contains_charm_toggle(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")

        self.assertIn('id="charm-toggles"', rendered)
        self.assertIn("toggleCharm", rendered)

    def test_rendered_html_contains_charm_search(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")

        self.assertIn('id="charm-search"', rendered)
        self.assertIn('placeholder="Search charms & interfaces', rendered)
        self.assertIn("filterCharms", rendered)
        self.assertIn(".charm-search", rendered)

    def test_rendered_html_contains_in_graph_search_and_hover(self):
        """The search box must also highlight/dim canvas nodes, and hover
        from the charm list must highlight a node's connected neighbours/edges."""
        rendered = visualizer._render_html(self.graph, "Sample charms")
        # Search-driven canvas highlight
        self.assertIn("applySearchFilter", rendered)
        self.assertIn("nodeMatchesSearch", rendered)
        self.assertIn("resetOpacity", rendered)
        # Hover highlight (from charm list only, not canvas nodes)
        self.assertIn("hoverHighlight", rendered)
        self.assertIn("hoverRestore", rendered)
        self.assertIn("mouseenter", rendered)
        self.assertIn("mouseleave", rendered)
        # Neighbour map for hover
        self.assertIn("neighbours", rendered)
        # Canvas nodes must NOT have mouseenter/mouseleave bound to them
        self.assertNotIn('node.on("mouseenter"', rendered)
        self.assertNotIn('node.on("mouseleave"', rendered)

    def test_rendered_html_contains_integration_edge_labels(self):
        """Integration links must show the interface name as a label on the canvas."""
        rendered = visualizer._render_html(self.graph, "Sample charms")
        self.assertIn("link-label", rendered)
        self.assertIn("linkLabel", rendered)
        self.assertIn("link-labels", rendered)

    def test_rendered_html_contains_theme_toggle(self):
        """The HTML must include a theme toggle button and light theme CSS."""
        rendered = visualizer._render_html(self.graph, "Sample charms")
        self.assertIn('id="btn-theme"', rendered)
        self.assertIn('data-theme="light"', rendered)
        self.assertIn("toggleTheme", rendered)
        self.assertIn("recolor", rendered)
        self.assertIn("getExportBg", rendered)
        self.assertIn("localStorage", rendered)

    def test_rendered_html_contains_keyboard_shortcuts(self):
        """The HTML must include keyboard shortcut handling and fitToScreen."""
        rendered = visualizer._render_html(self.graph, "Sample charms")
        self.assertIn("fitToScreen", rendered)
        self.assertIn("keydown", rendered)
        # All documented shortcuts must be present in the handler
        for key in ('"/"', '"Escape"', '"?"', '"+"', '"-"', '"0"', '"f"', '"t"', '"h"'):
            self.assertIn(key, rendered, f"shortcut key {key} not found in rendered HTML")
        # Help panel must document shortcuts
        self.assertIn("Keyboard shortcuts", rendered)

    def test_export_svg_xml_declaration_is_single_line(self):
        """Regression: the inline JS must not contain a literal newline inside
        the single-quoted <?xml ...?> string in exportSVG(), otherwise the
        whole <script> fails to parse and no charms render."""
        rendered = visualizer._render_html(self.graph, "Sample charms")
        import re
        self.assertTrue(
            re.search(r"<\?xml version=.1\.0. encoding=.UTF-8.\?>\\n'", rendered),
            "exportSVG xml declaration should use a JS escape (\\n), not a literal newline",
        )

    def test_rendered_html_contains_hide_unconnected_toggle(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")

        self.assertIn('id="btn-hide-unconnected"', rendered)
        self.assertIn("hideUnconnected", rendered)
        self.assertIn("relationConnected", rendered)

    def test_rendered_html_contains_export_buttons(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")

        for btn in ("btn-export-svg", "btn-export-png", "btn-export-json"):
            self.assertIn(f'id="{btn}"', rendered)
        self.assertIn("exportSVG", rendered)
        self.assertIn("exportPNG", rendered)
        self.assertIn("exportJSON", rendered)

    def test_rendered_html_contains_export_helpers(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")

        # Core building blocks for in-browser export are present.
        self.assertIn("buildExportSVG", rendered)
        self.assertIn("inlineSvgStyles", rendered)
        self.assertIn("downloadBlob", rendered)
        self.assertIn("XMLSerializer", rendered)
        self.assertIn("toBlob", rendered)

    def test_rendered_html_contains_lint_issues_panel(self):
        """The HTML template must have the Issues panel and lint JS."""
        rendered = visualizer._render_html(self.graph, "Sample charms")
        self.assertIn('id="issues-card"', rendered)
        self.assertIn('id="issues-list"', rendered)
        self.assertIn("LINT_WARNINGS", rendered)
        self.assertIn("highlightLintWarning", rendered)
        self.assertIn("clearLintHighlight", rendered)
        self.assertIn("findNodesForWarning", rendered)

    def test_rendered_html_contains_lint_warnings_data(self):
        """When rendered via render(), GRAPH_DATA must contain lint_warnings."""
        orphan = _charm("solo", requires={"db": "mysql"})
        rendered = visualizer.render([orphan], "solo", fmt="html")
        # The lint warning should be embedded in the data.
        self.assertIn("lint_warnings", rendered)
        self.assertIn("orphan", rendered)
        self.assertIn("no charm provides it", rendered)

    def test_rendered_html_lint_panel_hidden_when_no_warnings(self):
        """When there are no lint warnings, the issues card is removed by JS."""
        matched = [
            _charm("app", requires={"db": "mysql"}),
            _charm("db", provides={"db": "mysql"}),
        ]
        rendered = visualizer.render(matched, "matched", fmt="html")
        self.assertIn("lint_warnings", rendered)
        self.assertIn("issuesCard.remove", rendered)

    def test_rendered_html_lint_badge_on_title_card(self):
        """The title card should show an issue badge when warnings exist."""
        orphan = _charm("solo", requires={"db": "mysql"})
        rendered = visualizer.render([orphan], "solo", fmt="html")
        self.assertIn("issue-badge", rendered)
        self.assertIn("issue(s)", rendered)

    def test_rendered_html_contains_lint_help_text(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")
        self.assertIn("Issues</b>", rendered)

    def test_unconnected_relation_has_no_integration_link(self):
        lone = _charm("solo", requires={"db": "mysql"})
        graph = visualizer.build_combined_graph([lone])

        node_by_id = {n["id"]: n for n in graph["nodes"]}
        integrations = [
            link for link in graph["links"] if link["kind"] == "integration"
        ]
        self.assertEqual(integrations, [])

        relation_node = next(
            n for n in graph["nodes"] if n["type"] == "relation"
        )
        self.assertEqual(relation_node["role"], "requires")

    def test_cluster_spacing_scales_with_relation_count(self):
        """Cluster spacing should be larger for charms with more relations."""
        sparse = _charm("a", requires={"x": "iface"})
        dense = _charm(
            "b",
            requires={"r1": "i1", "r2": "i2", "r3": "i3",
                      "r4": "i4", "r5": "i5", "r6": "i6",
                      "r7": "i7", "r8": "i8", "r9": "i9", "r10": "i10"},
        )
        graph = visualizer.build_combined_graph([sparse, dense])
        charm_nodes = [n for n in graph["nodes"] if n["type"] == "charm"]
        self.assertEqual(len(charm_nodes), 2)
        x0, x1 = charm_nodes[0]["x"], charm_nodes[1]["x"]
        spacing = abs(x1 - x0)
        # Dense charm has 10 relations, so spacing should be at least
        # 120 * (10 + 2) = 1440, well above the old fixed 600.
        self.assertGreater(spacing, 600,
                           f"spacing {spacing} should exceed old fixed 600 for dense charms")

    def test_cluster_spacing_floor_for_sparse_charms(self):
        """Sparse charms should use the minimum spacing floor."""
        a = _charm("a", requires={"x": "iface"})
        b = _charm("b", provides={"x": "iface"})
        graph = visualizer.build_combined_graph([a, b])
        charm_nodes = [n for n in graph["nodes"] if n["type"] == "charm"]
        spacing = abs(charm_nodes[1]["x"] - charm_nodes[0]["x"])
        # 1 relation each → max_rels=1, spacing = max(450, 120*3) = 450
        self.assertEqual(spacing, 450)

    def test_radial_seeding_spreads_relation_nodes(self):
        """Relation nodes should be seeded at different positions around the charm."""
        charm = _charm(
            "multi",
            requires={"r1": "i1", "r2": "i2", "r3": "i3",
                      "r4": "i4", "r5": "i5"},
        )
        graph = visualizer.build_combined_graph([charm])
        charm_node = next(n for n in graph["nodes"] if n["type"] == "charm")
        rel_nodes = [n for n in graph["nodes"] if n["type"] == "relation"]
        self.assertEqual(len(rel_nodes), 5)
        # Each relation node should have a different (x, y) from the charm node
        for rn in rel_nodes:
            self.assertNotEqual((rn["x"], rn["y"]), (charm_node["x"], charm_node["y"]),
                                "relation node should not be seeded at charm position")
        # All relation nodes should have distinct positions
        positions = {(rn["x"], rn["y"]) for rn in rel_nodes}
        self.assertEqual(len(positions), len(rel_nodes),
                         "all relation nodes should be seeded at distinct positions")
        # Relation nodes should be roughly 90px from the charm (radial)
        import math
        for rn in rel_nodes:
            dist = math.sqrt((rn["x"] - charm_node["x"])**2 + (rn["y"] - charm_node["y"])**2)
            self.assertAlmostEqual(dist, 90, delta=5,
                                   msg=f"relation node should be ~90px from charm, got {dist}")


class FormatRenderTests(unittest.TestCase):
    """Smoke tests for the non-HTML output formats."""

    @classmethod
    def setUpClass(cls):
        cls.models = [
            visualizer.inspect_charm(path)
            for path in visualizer._find_charm_dirs(SAMPLE_CHARMS)
        ]
        # Two charms that integrate, so integrations appear in output.
        cls.pair = [
            _charm("app", requires={"db": "mysql"}),
            _charm("database", provides={"db": "mysql"}),
        ]

    def test_render_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            visualizer.render(self.pair, "pair", fmt="bogus")

    def test_json_output_includes_nodes_and_links(self):
        out = visualizer.render(self.pair, "pair", fmt="json")
        import json as _json
        payload = _json.loads(out)
        self.assertIn("nodes", payload)
        self.assertIn("links", payload)
        self.assertEqual(payload["title"], "pair")
        self.assertTrue(any(n["type"] == "charm" for n in payload["nodes"]))
        self.assertTrue(
            any(l["kind"] == "integration" for l in payload["links"])
        )

    def test_json_output_is_valid_json_for_sample_charms(self):
        import json as _json
        out = visualizer.render(self.models, "all", fmt="json")
        payload = _json.loads(out)
        self.assertEqual(len(payload["charms"]), len(self.models))

    def test_dot_output_is_well_formed(self):
        out = visualizer.render(self.pair, "pair", fmt="dot")
        self.assertTrue(out.lstrip().startswith("digraph"))
        self.assertIn("}", out)
        # Both charms and at least one integration link present.
        self.assertIn("app", out)
        self.assertIn("database", out)
        self.assertIn("mysql", out)
        self.assertIn(visualizer.INTEGRATION_COLOR, out)

    def test_dot_escapes_quotes(self):
        weird = _charm('na"me', requires={"db": "mysql"})
        out = visualizer.render([weird], "weird", fmt="dot")
        # The embedded quote must be escaped, not break the graph string.
        self.assertIn('\\"', out)

    def test_mermaid_output_uses_flowchart(self):
        out = visualizer.render(self.pair, "pair", fmt="mermaid")
        self.assertIn("flowchart LR", out)
        self.assertIn("classDef charm", out)
        self.assertIn("classDef rel-requires", out)
        self.assertIn("classDef rel-provides", out)
        # Integration edges carry the interface name as a (quoted) label.
        self.assertIn('|"mysql"|', out)
        # Integration endpoint names are referenced.
        self.assertIn("db", out)

    def test_mermaid_title_block(self):
        out = visualizer.render(self.pair, "My Pair", fmt="mermaid")
        self.assertIn("title: My Pair", out)

    def test_svg_output_is_valid_xml(self):
        import xml.etree.ElementTree as ET
        out = visualizer.render(self.pair, "pair", fmt="svg")
        self.assertTrue(out.lstrip().startswith("<?xml"))
        # Should parse without raising.
        root = ET.fromstring(out)
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        # Background rect + at least one circle (node) and one line (link).
        circles = root.findall(
            ".//{http://www.w3.org/2000/svg}circle"
        )
        lines = root.findall(
            ".//{http://www.w3.org/2000/svg}line"
        )
        self.assertGreater(len(circles), 0)
        self.assertGreater(len(lines), 0)

    def test_svg_output_for_sample_charms_parses(self):
        import xml.etree.ElementTree as ET
        out = visualizer.render(self.models, "all", fmt="svg")
        ET.fromstring(out)  # must not raise

    def test_render_format_dispatch_matches_each_format(self):
        for fmt in visualizer.SUPPORTED_FORMATS:
            out = visualizer.render(self.pair, "pair", fmt=fmt)
            self.assertIsInstance(out, str)
            self.assertTrue(out)


class EscapingTests(unittest.TestCase):
    """Regression tests for output escaping across all formats."""

    @classmethod
    def setUpClass(cls):
        # A charm whose name and interface contain characters that are
        # dangerous in each output format: quotes, angle brackets, pipe,
        # the </script> sequence, and a newline.
        cls.weird = visualizer.CharmModel(
            name='char</script>m & "co"',
            summary='has </script> in it',
            description='line1\nline2 </script>',
            subordinate=False,
            relations=[
                {
                    "endpoint": 'ep|pipe',
                    "role": "requires",
                    "interface": 'if<br>x',
                    "limit": None,
                    "scope": None,
                    "optional": False,
                }
            ],
            stats={"relations": 1, "subordinate": False},
        )
        cls.provider = visualizer.CharmModel(
            name="provider",
            summary="",
            description="",
            subordinate=False,
            relations=[
                {
                    "endpoint": 'ep|pipe',
                    "role": "provides",
                    "interface": 'if<br>x',
                    "limit": None,
                    "scope": None,
                    "optional": False,
                }
            ],
            stats={"relations": 1, "subordinate": False},
        )
        cls.models = [cls.weird, cls.provider]

    def test_html_escapes_script_close_tag(self):
        """A </script> in charm metadata must not break the inline <script>."""
        html = visualizer.render(self.models, "test", fmt="html")
        # The raw </script> sequence from the data must be escaped.
        # Find the GRAPH_DATA line and verify no raw </script> inside it.
        gd_start = html.index("const GRAPH_DATA = ")
        gd_end = html.index(";\n", gd_start)
        data_line = html[gd_start:gd_end]
        self.assertNotIn("</script>", data_line)
        # The escaped form <\/script> should be present instead.
        self.assertIn("<\\/script>", data_line)

    def test_mermaid_escapes_quotes_in_labels(self):
        out = visualizer.render(self.models, 'ti"tle', fmt="mermaid")
        # Double quotes in the title and node labels must be escaped.
        self.assertNotIn('ti"tle', out)
        self.assertIn("#quot;", out)

    def test_mermaid_escapes_pipe_in_interface_label(self):
        out = visualizer.render(self.models, "test", fmt="mermaid")
        # The pipe in the endpoint name must not appear unescaped inside
        # an edge label (it would terminate the label early). Edge labels
        # are now quoted so the pipe is safe inside the quotes.
        # Verify the integration edge label is quoted.
        self.assertIn('|"if<br>x"|', out)

    def test_dot_escapes_newlines(self):
        """Newlines in charm names must be escaped to avoid breaking DOT."""
        import re
        weird = visualizer.CharmModel(
            name="line\nbreak",
            summary="",
            description="",
            subordinate=False,
            relations=[],
            stats={"relations": 0, "subordinate": False},
        )
        out = visualizer.render([weird], "test", fmt="dot")
        # The label line must not contain a literal newline inside the quoted string.
        # Find the node label line.
        match = re.search(r'"line\\nbreak"', out)
        self.assertIsNotNone(match, "newline in name should be escaped as \\n in DOT")

    def test_dot_escapes_angle_brackets(self):
        out = visualizer.render(self.models, "test", fmt="dot")
        # Angle brackets are not special in DOT labels, but must not break
        # the attribute syntax. The label should contain them literally.
        self.assertIn("if<br>x", out)

    def test_svg_escapes_angle_brackets(self):
        """Angle brackets in charm names must be XML-escaped in SVG."""
        import xml.etree.ElementTree as ET
        out = visualizer.render(self.models, "test", fmt="svg")
        # Must parse as valid XML.
        root = ET.fromstring(out)
        # The <br> in the interface must be escaped, not parsed as a tag.
        texts = [t.text for t in root.iter("{http://www.w3.org/2000/svg}text") if t.text]
        joined = " ".join(texts)
        self.assertIn("if<br>x", joined)

    def test_json_is_valid_with_special_chars(self):
        import json as _json
        out = visualizer.render(self.models, "test", fmt="json")
        payload = _json.loads(out)
        self.assertEqual(payload["charms"][0]["name"], 'char</script>m & "co"')


class SubordinateTests(unittest.TestCase):
    """Tests for subordinate charm visualization across formats."""

    @classmethod
    def setUpClass(cls):
        # A subordinate charm (filesystem-client style) with a container-scope
        # relation, and a principal charm it integrates with.
        cls.subordinate = _charm(
            "filesystem-client",
            requires={"filesystem": "filesystem_info"},
            subordinate=True,
            scopes={"filesystem": "container"},
        )
        cls.principal = _charm(
            "lustre-server",
            provides={"filesystem": "filesystem_info"},
        )
        cls.models = [cls.subordinate, cls.principal]
        cls.graph = visualizer.build_combined_graph(cls.models)

    def test_inspect_charm_reads_subordinate_flag(self):
        from pathlib import Path
        fc_dir = SAMPLE_CHARMS / "filesystem-charms" / "filesystem-client"
        model = visualizer.inspect_charm(fc_dir)
        self.assertTrue(model["subordinate"])
        self.assertTrue(model["stats"]["subordinate"])
        # A non-subordinate charm is False, not missing.
        ls_dir = SAMPLE_CHARMS / "filesystem-charms" / "lustre-server"
        ls_model = visualizer.inspect_charm(ls_dir)
        self.assertFalse(ls_model["subordinate"])

    def test_charm_node_carries_subordinate_flag(self):
        charm_nodes = [n for n in self.graph["nodes"] if n["type"] == "charm"]
        sub = next(n for n in charm_nodes if n["name"] == "filesystem-client")
        prin = next(n for n in charm_nodes if n["name"] == "lustre-server")
        self.assertTrue(sub["subordinate"])
        self.assertFalse(prin["subordinate"])

    def test_relation_links_carry_scope(self):
        rel_links = [l for l in self.graph["links"] if l["kind"] == "relation"]
        # The filesystem relation on the subordinate has scope=container.
        fs_link = next(
            l for l in rel_links
            if "filesystem" in l["target"] and l.get("scope") == "container"
        )
        self.assertEqual(fs_link.get("scope"), "container")
        # Other relation links have scope=None.
        non_container = [
            l for l in rel_links if l.get("scope") != "container"
        ]
        self.assertTrue(non_container)

    def test_html_renders_subordinate_marker_and_container_edge(self):
        html = visualizer.render(self.models, "sub", fmt="html")
        # Legend entry
        self.assertIn("Subordinate charm", html)
        self.assertIn("container-scope relation", html)
        # CSS for dashed subordinate ring and container link
        self.assertIn("subordinate-ring", html)
        self.assertIn("link.relation.container", html)
        # JS sublabel branch
        self.assertIn('"subordinate"', html)
        # Help text
        self.assertIn("Subordinate charms", html)

    def test_dot_marks_subordinate_node_and_container_edge(self):
        out = visualizer.render(self.models, "sub", fmt="dot")
        # Subordinate charm node uses dashed style.
        self.assertIn("filled,dashed", out)
        # Container-scope relation edge uses dashed style.
        self.assertIn('style="dashed"', out)

    def test_mermaid_marks_subordinate_node_and_container_edge(self):
        out = visualizer.render(self.models, "sub", fmt="mermaid")
        # Subordinate node class and dashed border classDef.
        self.assertIn(":::subordinate", out)
        self.assertIn("stroke-dasharray: 4 3", out)
        # Container-scope edge is dotted with a (quoted) label.
        self.assertIn('-.->|"container"|', out)
        # linkStyle override for container edges.
        self.assertIn("stroke-dasharray: 5 4", out)

    def test_svg_marks_subordinate_node_and_container_edge(self):
        import xml.etree.ElementTree as ET
        out = visualizer.render(self.models, "sub", fmt="svg")
        root = ET.fromstring(out)
        # Subordinate charm node has a dashed circle (stroke-dasharray attribute).
        circles = root.findall(".//{http://www.w3.org/2000/svg}circle")
        dashed_circles = [
            c for c in circles if c.get("stroke-dasharray") == "4 3"
        ]
        self.assertTrue(dashed_circles, "no dashed circles for subordinate charm")
        # "subordinate" sublabel text is present.
        texts = root.findall(".//{http://www.w3.org/2000/svg}text")
        sub_labels = [t for t in texts if t.text == "subordinate"]
        self.assertTrue(sub_labels)
        # Container-scope line is dashed.
        lines = root.findall(".//{http://www.w3.org/2000/svg}line")
        dashed_lines = [
            l for l in lines if l.get("stroke-dasharray") == "5 4"
        ]
        self.assertTrue(dashed_lines, "no dashed lines for container-scope relations")

    def test_json_carries_subordinate_and_scope(self):
        import json as _json
        out = visualizer.render(self.models, "sub", fmt="json")
        payload = _json.loads(out)
        charm_nodes = [n for n in payload["nodes"] if n["type"] == "charm"]
        sub = next(n for n in charm_nodes if n["name"] == "filesystem-client")
        self.assertTrue(sub["subordinate"])
        rel_links = [l for l in payload["links"] if l["kind"] == "relation"]
        fs_link = next(
            l for l in rel_links
            if "filesystem" in l["target"] and l.get("scope") == "container"
        )
        self.assertEqual(fs_link.get("scope"), "container")


class LintTests(unittest.TestCase):
    """Tests for the --lint diagnostic mode."""

    def test_no_warnings_when_all_endpoints_matched(self):
        models = [
            _charm("app", requires={"db": "mysql"}),
            _charm("db", provides={"db": "mysql"}),
        ]
        warnings = visualizer.lint_charms(models)
        self.assertEqual(warnings, [])

    def test_orphan_requires_detected(self):
        models = [_charm("solo", requires={"db": "mysql"})]
        warnings = visualizer.lint_charms(models)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["kind"], "orphan")
        self.assertEqual(warnings[0]["charm"], "solo")
        self.assertEqual(warnings[0]["endpoint"], "db")
        self.assertIn("no charm provides it", warnings[0]["message"])

    def test_orphan_provides_detected(self):
        models = [_charm("solo", provides={"db": "mysql"})]
        warnings = visualizer.lint_charms(models)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["kind"], "orphan")
        self.assertIn("no charm requires it", warnings[0]["message"])

    def test_duplicate_provider_detected(self):
        models = [
            _charm("integrator", provides={"smtp": "smtp", "smtp-legacy": "smtp"}),
        ]
        warnings = visualizer.lint_charms(models)
        dup = [w for w in warnings if w["kind"] == "duplicate-provider"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["charm"], "integrator")
        self.assertIn("smtp", dup[0]["endpoint"])
        self.assertIn("smtp-legacy", dup[0]["endpoint"])

    def test_limit_not_exceeded_when_within_limit(self):
        models = [
            _charm("db", provides={"db": "mysql"}, limits={"db": 2}),
            _charm("app1", requires={"db": "mysql"}),
        ]
        warnings = visualizer.lint_charms(models)
        limit_warnings = [w for w in warnings if w["kind"] == "limit-exceeded"]
        self.assertEqual(limit_warnings, [])

    def test_limit_exceeded_when_over_limit(self):
        models = [
            _charm("db", provides={"db": "mysql"}, limits={"db": 1}),
            _charm("app1", requires={"db": "mysql"}),
            _charm("app2", requires={"db": "mysql"}),
        ]
        warnings = visualizer.lint_charms(models)
        limit_warnings = [w for w in warnings if w["kind"] == "limit-exceeded"]
        self.assertEqual(len(limit_warnings), 1)
        self.assertEqual(limit_warnings[0]["charm"], "db")
        self.assertIn("limit 1", limit_warnings[0]["message"])
        self.assertIn("2 charm(s)", limit_warnings[0]["message"])

    def test_peers_are_not_linted_as_orphans(self):
        models = [_charm("solo", peers={"cluster": "cluster"})]
        warnings = visualizer.lint_charms(models)
        self.assertEqual(warnings, [])

    def test_format_lint_warnings_empty(self):
        out = visualizer.format_lint_warnings([])
        self.assertIn("No issues found", out)

    def test_format_lint_warnings_nonempty(self):
        warnings = visualizer.lint_charms([_charm("solo", requires={"db": "mysql"})])
        out = visualizer.format_lint_warnings(warnings)
        self.assertIn("[ORPHAN]", out)
        self.assertIn("1 warning(s)", out)

    def test_lint_finds_issues_in_sample_charms(self):
        models = [
            visualizer.inspect_charm(path)
            for path in visualizer._find_charm_dirs(SAMPLE_CHARMS)
        ]
        warnings = visualizer.lint_charms(models)
        self.assertGreater(len(warnings), 0)
        kinds = {w["kind"] for w in warnings}
        self.assertIn("orphan", kinds)
        # smtp-integrator has duplicate provides on interface 'smtp'
        self.assertIn("duplicate-provider", kinds)

    def test_cli_lint_mode_exits_zero_without_strict(self):
        models_dir = str(SAMPLE_CHARMS)
        rc = visualizer.main(["--all", models_dir, "--lint"])
        self.assertEqual(rc, 0)

    def test_cli_lint_strict_exits_nonzero_with_warnings(self):
        models_dir = str(SAMPLE_CHARMS)
        rc = visualizer.main(["--all", models_dir, "--lint", "--strict"])
        self.assertNotEqual(rc, 0)

    def test_cli_lint_strict_exits_zero_without_warnings(self):
        models = [
            _charm("app", requires={"db": "mysql"}),
            _charm("db", provides={"db": "mysql"}),
        ]
        import tempfile, json, os
        with tempfile.TemporaryDirectory() as tmpdir:
            for m in models:
                cdir = Path(tmpdir) / m["name"]
                cdir.mkdir()
                meta = {"name": m["name"]}
                for r in m["relations"]:
                    meta.setdefault(r["role"], {})[r["endpoint"]] = {
                        "interface": r["interface"]
                    }
                (cdir / "metadata.yaml").write_text(
                    __import__("yaml").dump(meta), encoding="utf-8"
                )
            rc = visualizer.main(["--all", tmpdir, "--lint", "--strict"])
            self.assertEqual(rc, 0)

    def test_cli_version_flag_prints_version(self):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.suppress(SystemExit) as cm:
            with contextlib.redirect_stdout(buf):
                visualizer.main(["--version"])
        output = buf.getvalue().strip()
        self.assertIn(visualizer.__version__, output)


class CharmhubFetchTests(unittest.TestCase):
    """Tests for the --charmhub fetch mode (with mocked HTTP)."""

    SAMPLE_METADATA_YAML = (
        "name: test-charm\n"
        "summary: A test charm\n"
        "description: This is a test.\n"
        "requires:\n"
        "  db:\n"
        "    interface: mysql\n"
        "provides:\n"
        "  web:\n"
        "    interface: http\n"
    )

    def _mock_api_response(self, metadata_yaml=None):
        import json as _json, io
        metadata_yaml = metadata_yaml or self.SAMPLE_METADATA_YAML
        payload = {
            "default-release": {
                "channel": {"name": "latest/stable", "risk": "stable"},
                "revision": {"metadata-yaml": metadata_yaml, "version": "1.0"},
            }
        }
        return io.BytesIO(_json.dumps(payload).encode("utf-8"))

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self._orig_cache_dir = visualizer.CACHE_DIR
        visualizer.CACHE_DIR = Path(self.tmpdir) / "cache"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        visualizer.CACHE_DIR = self._orig_cache_dir

    def test_fetch_charmhub_charm_returns_model(self):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._mock_api_response().read()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.object(visualizer.urllib.request, "urlopen", return_value=mock_resp):
            model = visualizer.fetch_charmhub_charm("test-charm")
        self.assertEqual(model["name"], "test-charm")
        self.assertEqual(model["summary"], "A test charm")
        self.assertEqual(len(model["relations"]), 2)
        self.assertIn("charmhub:test-charm", model["meta_path"])

    def test_fetch_charmhub_charm_caches(self):
        from unittest.mock import patch, MagicMock
        call_count = 0
        def fake_urlopen(*args, **kw):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.read.return_value = self._mock_api_response().read()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp
        with patch.object(visualizer.urllib.request, "urlopen", side_effect=fake_urlopen):
            m1 = visualizer.fetch_charmhub_charm("cached-charm")
            m2 = visualizer.fetch_charmhub_charm("cached-charm")
        self.assertEqual(call_count, 1, "second call should hit cache, not network")
        self.assertEqual(m1["name"], m2["name"])

    def test_fetch_charmhub_charm_no_cache_flag(self):
        from unittest.mock import patch, MagicMock
        call_count = 0
        def fake_urlopen(*args, **kw):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.read.return_value = self._mock_api_response().read()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp
        with patch.object(visualizer.urllib.request, "urlopen", side_effect=fake_urlopen):
            visualizer.fetch_charmhub_charm("no-cache-charm")
            # Delete cache to simulate --no-cache
            visualizer._cache_path("no-cache-charm", None).unlink()
            visualizer.fetch_charmhub_charm("no-cache-charm")
        self.assertEqual(call_count, 2, "both calls should hit network when cache deleted")

    def test_fetch_charmhub_charm_404_raises_error(self):
        import urllib.error
        from unittest.mock import patch
        error = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )
        with patch.object(visualizer.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(visualizer.CharmhubFetchError) as ctx:
                visualizer.fetch_charmhub_charm("nonexistent")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_fetch_charmhub_charm_network_error_raises(self):
        import urllib.error
        from unittest.mock import patch
        error = urllib.error.URLError("connection refused")
        with patch.object(visualizer.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(visualizer.CharmhubFetchError) as ctx:
                visualizer.fetch_charmhub_charm("unreachable")
        self.assertIn("network error", str(ctx.exception).lower())

    def test_fetch_charmhub_charm_with_channel(self):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._mock_api_response().read()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.object(visualizer.urllib.request, "urlopen", return_value=mock_resp) as mock_open:
            model = visualizer.fetch_charmhub_charm("test-charm", channel="latest/edge")
        self.assertEqual(model["name"], "test-charm")
        self.assertIn("latest/edge", model["meta_path"])
        # Verify the channel was in the URL
        called_url = mock_open.call_args[0][0].full_url
        self.assertIn("channel", called_url)

    def test_fetch_charmhub_charm_subordinate(self):
        from unittest.mock import patch, MagicMock
        yaml_str = (
            "name: sub-charm\n"
            "subordinate: true\n"
            "requires:\n"
            "  juju-info:\n"
            "    interface: juju-info\n"
            "    scope: container\n"
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = self._mock_api_response(yaml_str).read()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch.object(visualizer.urllib.request, "urlopen", return_value=mock_resp):
            model = visualizer.fetch_charmhub_charm("sub-charm")
        self.assertTrue(model["subordinate"])
        self.assertTrue(model["stats"]["subordinate"])


if __name__ == "__main__":
    unittest.main()
