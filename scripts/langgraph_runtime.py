#!/usr/bin/env python3
"""Optional LangGraph orchestration for the guarded runtime DAG."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, TypedDict


class LangGraphUnavailable(RuntimeError):
    """Raised when the optional LangGraph runtime is not installed."""


class ExecutionState(TypedDict, total=False):
    """Privacy-safe orchestration state persisted by LangGraph."""

    run_id: str
    run_dir: str
    operation: str
    result_code: int
    halted: bool
    last_node: str


NodeHandler = Callable[[], int]


def _load_langgraph() -> tuple[object, object, object, object]:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise LangGraphUnavailable(
            "LangGraph runtime is not installed. Install requirements-langgraph.txt "
            "or run with --engine native."
        ) from exc
    return StateGraph, START, END, SqliteSaver


def run_guarded_graph(
    *,
    run_dir: Path,
    run_id: str,
    operation: str,
    handlers: dict[str, NodeHandler],
) -> int:
    """Execute the five guarded runtime nodes with durable LangGraph checkpoints.

    Handlers own artifact staging, Guard execution, promotion, and state.json updates.
    LangGraph only persists privacy-safe orchestration metadata and routing state.
    """

    StateGraph, START, END, SqliteSaver = _load_langgraph()
    node_order = (
        "prepare",
        "generate_report",
        "validate_report",
        "evaluate_report",
        "finalize",
    )
    missing = [node_id for node_id in node_order if node_id not in handlers]
    if missing:
        raise ValueError(f"Missing LangGraph handlers: {', '.join(missing)}")

    graph = StateGraph(ExecutionState)

    def guarded_node(node_id: str) -> Callable[[ExecutionState], ExecutionState]:
        def invoke(_: ExecutionState) -> ExecutionState:
            result_code = handlers[node_id]()
            return {
                "result_code": result_code,
                "halted": result_code != 0,
                "last_node": node_id,
            }

        return invoke

    for node_id in node_order:
        graph.add_node(node_id, guarded_node(node_id))

    graph.add_edge(START, "prepare")
    for index, node_id in enumerate(node_order[:-1]):
        next_node = node_order[index + 1]

        def route(state: ExecutionState, *, target: str = next_node) -> str:
            return END if state.get("halted", False) else target

        graph.add_conditional_edges(node_id, route, [next_node, END])
    graph.add_edge(node_order[-1], END)

    checkpoint_path = run_dir / "langgraph-checkpoints.sqlite"
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        checkpointer = SqliteSaver(connection)
        compiled = graph.compile(checkpointer=checkpointer)
        result = compiled.invoke(
            {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "operation": operation,
                "result_code": 0,
                "halted": False,
                "last_node": "",
            },
            {"configurable": {"thread_id": run_id}},
        )
    finally:
        connection.close()
    return int(result.get("result_code", 1))
