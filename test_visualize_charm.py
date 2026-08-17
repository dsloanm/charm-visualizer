import unittest
from pathlib import Path

import visualize_charm as visualizer


SAMPLE_CHARMS = Path(__file__).parent / "sample-charms"


def _charm(name, requires=None, provides=None, peers=None, subordinate=False, scopes=None):
    """Build a minimal CharmModel with the given relations.

    ``scopes`` optionally maps endpoint names to a relation scope (e.g.
    ``"container"``); endpoints not listed default to ``None``.
    """
    scopes = scopes or {}
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
                    "limit": None,
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
        self.assertIn('placeholder="Filter charms', rendered)
        self.assertIn("filterCharms", rendered)
        self.assertIn(".charm-search", rendered)

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
        # Integration edges carry the interface name as a label.
        self.assertIn("|mysql|", out)
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
        # Container-scope edge is dotted with a label.
        self.assertIn("-.->|container|", out)
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


if __name__ == "__main__":
    unittest.main()
