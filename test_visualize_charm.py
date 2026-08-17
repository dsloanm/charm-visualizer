import unittest
from pathlib import Path

import visualize_charm as visualizer


SAMPLE_CHARMS = Path(__file__).parent / "sample-charms"


def _charm(name, requires=None, provides=None, peers=None):
    """Build a minimal CharmModel with the given relations."""
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
                    "scope": None,
                    "optional": False,
                }
            )
    return visualizer.CharmModel(
        name=name,
        summary="",
        description="",
        relations=relations,
        stats={"relations": len(relations)},
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

    def test_rendered_html_contains_hide_unconnected_toggle(self):
        rendered = visualizer._render_html(self.graph, "Sample charms")

        self.assertIn('id="btn-hide-unconnected"', rendered)
        self.assertIn("hideUnconnected", rendered)
        self.assertIn("relationConnected", rendered)

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


if __name__ == "__main__":
    unittest.main()
