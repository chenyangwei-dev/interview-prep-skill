#!/usr/bin/env python3
"""Small dependency-graph primitives for the interview-prep workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


TERMINAL_SUCCESS = {"completed", "completed_empty", "completed_with_warning", "skipped"}


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    depends_on: tuple[str, ...] = ()
    guards: tuple[str, ...] = ("schema",)
    optional: bool = False
    generated: bool = False


class DagError(ValueError):
    """Raised when a graph declaration is invalid."""


class Dag:
    def __init__(self, nodes: Iterable[NodeSpec]) -> None:
        materialized = tuple(nodes)
        self.nodes = {node.node_id: node for node in materialized}
        if len(self.nodes) != len(materialized):
            raise DagError("DAG contains duplicate node IDs")
        self._validate()

    def _validate(self) -> None:
        for node in self.nodes.values():
            if not node.node_id:
                raise DagError("DAG node ID cannot be empty")
            if not node.guards:
                raise DagError(f"DAG node must declare at least one guard: {node.node_id}")
            unknown = sorted(set(node.depends_on) - set(self.nodes))
            if unknown:
                raise DagError(f"Unknown dependencies for {node.node_id}: {', '.join(unknown)}")
            if node.node_id in node.depends_on:
                raise DagError(f"Node cannot depend on itself: {node.node_id}")
        self.topological_order()

    def topological_order(self) -> list[str]:
        indegree = {node_id: 0 for node_id in self.nodes}
        children: dict[str, list[str]] = {node_id: [] for node_id in self.nodes}
        for node in self.nodes.values():
            indegree[node.node_id] = len(node.depends_on)
            for dependency in node.depends_on:
                children[dependency].append(node.node_id)

        ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        ordered: list[str] = []
        while ready:
            node_id = ready.pop(0)
            ordered.append(node_id)
            for child in sorted(children[node_id]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if len(ordered) != len(self.nodes):
            cyclic = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
            raise DagError(f"DAG contains a cycle involving: {', '.join(cyclic)}")
        return ordered

    def descendants(self, node_id: str) -> set[str]:
        if node_id not in self.nodes:
            raise DagError(f"Unknown node: {node_id}")
        children: dict[str, set[str]] = {name: set() for name in self.nodes}
        for node in self.nodes.values():
            for dependency in node.depends_on:
                children[dependency].add(node.node_id)
        found: set[str] = set()
        pending = list(children[node_id])
        while pending:
            child = pending.pop()
            if child in found:
                continue
            found.add(child)
            pending.extend(children[child])
        return found

    def ready(self, states: Mapping[str, str]) -> list[str]:
        ready: list[str] = []
        for node_id in self.topological_order():
            status = states.get(node_id, "pending")
            if status not in {"pending", "invalidated"}:
                continue
            if all(states.get(dep) in TERMINAL_SUCCESS for dep in self.nodes[node_id].depends_on):
                ready.append(node_id)
        return ready

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "node_id": node_id,
                "depends_on": list(self.nodes[node_id].depends_on),
                "guards": list(self.nodes[node_id].guards),
                "optional": self.nodes[node_id].optional,
                "generated": self.nodes[node_id].generated,
            }
            for node_id in self.topological_order()
        ]


CONTENT_DAG = Dag(
    (
        NodeSpec("load_job", guards=("schema", "path_policy")),
        NodeSpec("normalize_jd", ("load_job",), ("schema", "source_hash")),
        NodeSpec("normalize_resume", ("load_job",), ("schema", "source_hash")),
        NodeSpec(
            "material_audit",
            ("normalize_jd", "normalize_resume"),
            ("schema", "audit_truthfulness"),
        ),
        NodeSpec(
            "resolve_languages",
            ("normalize_jd", "normalize_resume"),
            ("schema", "language_basis"),
        ),
        NodeSpec(
            "extract_jd_evidence",
            ("material_audit", "resolve_languages"),
            ("schema", "provenance", "claim_rules", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "extract_resume_evidence",
            ("material_audit", "resolve_languages"),
            ("schema", "provenance", "claim_rules", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "merge_evidence_ledger",
            ("extract_jd_evidence", "extract_resume_evidence"),
            ("schema", "referential_integrity"),
        ),
        NodeSpec(
            "analyze_role",
            ("merge_evidence_ledger",),
            ("schema", "provenance", "claim_rules", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "analyze_candidate",
            ("merge_evidence_ledger",),
            ("schema", "provenance", "claim_rules", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "build_match_matrix",
            ("analyze_role", "analyze_candidate"),
            ("schema", "provenance", "match_policy", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "build_story_bank",
            ("analyze_candidate",),
            ("schema", "provenance", "contribution", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "generate_core_interview",
            ("analyze_role", "build_match_matrix", "build_story_bank"),
            ("schema", "provenance", "claim_rules", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "generate_system_design",
            ("analyze_role", "build_match_matrix"),
            ("schema", "provenance", "assumption_labels", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "generate_management_round",
            ("analyze_role", "build_match_matrix", "build_story_bank"),
            ("schema", "provenance", "contribution", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec(
            "generate_gap_learning",
            ("build_match_matrix",),
            ("schema", "provenance", "source_freshness", "semantic_grounding"),
            optional=True,
            generated=True,
        ),
        NodeSpec(
            "generate_strategy_pack",
            (
                "generate_core_interview",
                "generate_system_design",
                "generate_management_round",
                "generate_gap_learning",
            ),
            ("schema", "referential_integrity", "no_new_facts"),
            generated=True,
        ),
        NodeSpec(
            "assemble_report_model",
            ("merge_evidence_ledger", "build_match_matrix", "build_story_bank", "generate_strategy_pack"),
            ("schema", "referential_integrity", "global_consistency"),
        ),
        NodeSpec(
            "render_report",
            ("assemble_report_model",),
            ("schema", "claim_binding", "no_new_facts"),
        ),
        NodeSpec("validate_report", ("render_report",), ("input_hash", "html_validation")),
        NodeSpec(
            "evaluate_report",
            ("validate_report",),
            ("input_hash", "evaluation_pass"),
            optional=True,
        ),
        NodeSpec(
            "visual_qa",
            ("validate_report",),
            ("input_hash", "audit_truthfulness"),
            optional=True,
        ),
        NodeSpec(
            "finalize",
            ("evaluate_report", "visual_qa"),
            ("guard_completeness", "artifact_hashes"),
        ),
    )
)


RUNTIME_DAG = Dag(
    (
        NodeSpec("prepare", guards=("schema", "source_hash")),
        NodeSpec(
            "generate_report",
            ("prepare",),
            ("schema", "provenance", "claim_rules", "semantic_grounding"),
            generated=True,
        ),
        NodeSpec("validate_report", ("generate_report",), ("input_hash", "html_validation")),
        NodeSpec(
            "evaluate_report",
            ("validate_report",),
            ("input_hash", "evaluation_pass"),
            optional=True,
        ),
        NodeSpec(
            "finalize",
            ("evaluate_report",),
            ("guard_completeness", "artifact_hashes"),
        ),
    )
)
