from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS))

from langgraph_runtime import LangGraphUnavailable, run_guarded_graph  # noqa: E402


class LangGraphRuntimeTests(unittest.TestCase):
    def test_missing_dependency_has_actionable_message(self) -> None:
        if importlib.util.find_spec("langgraph") is not None:
            self.skipTest("LangGraph is installed")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(LangGraphUnavailable, "requirements-langgraph.txt"):
                run_guarded_graph(
                    run_dir=Path(directory),
                    run_id="missing-dependency",
                    operation="start",
                    handlers={},
                )

    @unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "LangGraph is unavailable")
    def test_graph_stops_after_a_guarded_node_returns_nonzero(self) -> None:
        visited: list[str] = []
        node_order = (
            "prepare",
            "generate_report",
            "validate_report",
            "evaluate_report",
            "finalize",
        )
        handlers = {}
        for node_id in node_order:
            result_code = 3 if node_id == "generate_report" else 0

            def handler(name: str = node_id, code: int = result_code) -> int:
                visited.append(name)
                return code

            handlers[node_id] = handler

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            result = run_guarded_graph(
                run_dir=run_dir,
                run_id="halt-test",
                operation="start",
                handlers=handlers,
            )
            self.assertEqual(result, 3)
            self.assertEqual(visited, ["prepare", "generate_report"])
            self.assertTrue((run_dir / "langgraph-checkpoints.sqlite").is_file())
