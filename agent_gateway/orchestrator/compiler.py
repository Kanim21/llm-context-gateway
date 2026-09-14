"""Compiles React-Flow canvas JSON into a validated Playbook. Enforces the
6 linear-chain rules with plain-language (workplace-metaphor) error copy —
no developer jargon reaches the UI."""

from __future__ import annotations

from agent_gateway.orchestrator.schema import GateConfig, Playbook, PlaybookStep, TeammateConfig


class CompilerError(Exception):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors
        super().__init__("; ".join(e["message"] for e in errors))


def compile_canvas(
    canvas_json: dict, *, playbook_id: str, name: str, description: str = "",
    workspace_id: str = "default",
) -> Playbook:
    nodes = {n["id"]: n for n in canvas_json.get("nodes", [])}
    edges = canvas_json.get("edges", [])

    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        outgoing.setdefault(edge["source"], []).append(edge["target"])

    errors: list[dict[str, str]] = []

    start_nodes = [n for n in nodes.values() if n["type"] == "start"]
    if len(start_nodes) != 1:
        errors.append({"node_id": "", "message": "A playbook needs exactly one starting point."})

    # Detect cycles using DFS
    def has_cycle_dfs(node_id: str, visited: set[str], rec_stack: set[str]) -> bool:
        visited.add(node_id)
        rec_stack.add(node_id)

        for next_id in outgoing.get(node_id, []):
            if next_id not in visited:
                if has_cycle_dfs(next_id, visited, rec_stack):
                    return True
            elif next_id in rec_stack:
                return True

        rec_stack.remove(node_id)
        return False

    visited_dfs: set[str] = set()
    has_cycle_flag = False
    if start_nodes:
        has_cycle_flag = has_cycle_dfs(start_nodes[0]["id"], visited_dfs, set())
        if has_cycle_flag:
            errors.append({
                "node_id": "",
                "message": "This playbook loops back on itself — playbooks run start to finish, once.",
            })

    # Build visited_order for linear traversal (following first edge only)
    seen: set[str] = set()
    visited_order: list[str] = []
    if start_nodes and not has_cycle_flag:
        current = start_nodes[0]["id"]
        while current is not None and current in nodes:
            if current in seen:
                break
            seen.add(current)
            visited_order.append(current)
            next_ids = outgoing.get(current, [])
            current = next_ids[0] if next_ids else None
    elif start_nodes:
        # If there's a cycle, still traverse from start for field validation
        current = start_nodes[0]["id"]
        while current is not None and current in nodes and current not in seen:
            seen.add(current)
            visited_order.append(current)
            next_ids = outgoing.get(current, [])
            current = next_ids[0] if next_ids else None

    for node_id, node in nodes.items():
        if node["type"] == "end":
            continue
        out_count = len(outgoing.get(node_id, []))
        if out_count == 0:
            errors.append({
                "node_id": node_id,
                "message": "This step doesn't lead anywhere — connect it to the next step or mark it as the end.",
            })
        elif out_count > 1:
            errors.append({
                "node_id": node_id,
                "message": "This step has two next steps — Playbooks run one step at a time.",
            })

    orphans = set(nodes) - seen
    for node_id in orphans:
        errors.append({
            "node_id": node_id,
            "message": "This step isn't connected to the playbook — attach it to the chain or remove it.",
        })

    for node_id in visited_order:
        node = nodes[node_id]
        if node["type"] == "approval_gate":
            if not node["data"].get("label", "").strip():
                errors.append({"node_id": node_id, "message": "This approval gate needs a label."})
        elif node["type"] == "teammate":
            if not node["data"].get("role", "").strip():
                errors.append({"node_id": node_id, "message": "This teammate needs a role."})
            if not node["data"].get("objective", "").strip():
                errors.append({"node_id": node_id, "message": "This teammate needs an objective."})

    if errors:
        raise CompilerError(errors)

    steps: list[PlaybookStep] = []
    for node_id in visited_order:
        node = nodes[node_id]
        if node["type"] == "teammate":
            data = node["data"]
            steps.append(PlaybookStep(
                step_id=node_id, type="teammate",
                teammate=TeammateConfig(
                    role=data["role"], objective=data["objective"], tier=data["tier"],
                    connected_apps=data.get("connected_apps", []),
                    knowledge=data.get("knowledge", []),
                ),
            ))
        elif node["type"] == "approval_gate":
            data = node["data"]
            steps.append(PlaybookStep(
                step_id=node_id, type="approval_gate",
                gate=GateConfig(label=data["label"], allow_edit=data.get("allow_edit", True)),
            ))
        # start/end nodes produce no PlaybookStep

    return Playbook(id=playbook_id, workspace_id=workspace_id, name=name,
                     description=description, steps=steps)
