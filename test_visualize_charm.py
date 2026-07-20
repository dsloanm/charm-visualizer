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


if __name__ == "__main__":
    unittest.main()
