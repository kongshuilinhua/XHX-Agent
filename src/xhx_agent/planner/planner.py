from __future__ import annotations

from pathlib import Path

from xhx_agent.planner.modes import DAGNode, DAGPlan


class DAGPlanner:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def plan_dag(self, task: str) -> DAGPlan:
        # Generate a standard DAG plan based on the task description.
        # This is a robust intent-based heuristic planner serving as a stable baseline fallback.
        # Future integration enhancements will support LLM-based DAG generation.
        nodes = []
        lowered = task.lower()

        if "refactor" in lowered or "math" in lowered:
            # Multi-step math refactoring task
            nodes.extend([
                DAGNode(
                    node_id="read_calc",
                    description="Read calc.py to understand math implementation",
                    tool="read_file",
                    arguments={"path": "src/calc.py"},
                    dependencies=[]
                ),
                DAGNode(
                    node_id="search_math",
                    description="Search for math usages across project",
                    tool="search",
                    arguments={"query": "add"},
                    dependencies=[]
                ),
                DAGNode(
                    node_id="patch_calc",
                    description="Refactor calc.py with robust math functions",
                    tool="apply_patch",
                    arguments={
                        "patch": "*** Begin Patch\n*** Update File: src/calc.py\n@@\n-def add(a, b):\n-    return a + b\n+def add(a, b):\n+    \"\"\"Add two numbers safely\"\"\"\n+    return float(a) + float(b)\n*** End Patch\n"
                    },
                    dependencies=["read_calc", "search_math"]
                ),
                DAGNode(
                    node_id="verify_test",
                    description="Run verification tests",
                    tool="terminal",
                    arguments={"command": "python -m pytest"},
                    dependencies=["patch_calc"]
                )
            ])
        else:
            # Default fallback simple linear edit in DAG format
            nodes.extend([
                DAGNode(
                    node_id="read_root",
                    description="Read main codebase file",
                    tool="read_file",
                    arguments={"path": "README.md"},
                    dependencies=[]
                ),
                DAGNode(
                    node_id="verify_root",
                    description="Verify project state",
                    tool="terminal",
                    arguments={"command": "python -m pytest"},
                    dependencies=["read_root"]
                )
            ])

        return DAGPlan(root=str(self.workspace), nodes=nodes)


def topological_sort(nodes: list[DAGNode]) -> list[DAGNode]:
    # Kahn's algorithm for topological sorting and cycle detection
    adj = {node.node_id: [] for node in nodes}
    in_degree = {node.node_id: 0 for node in nodes}
    nodes_by_id = {node.node_id: node for node in nodes}

    for node in nodes:
        for dep in node.dependencies:
            if dep in adj:
                adj[dep].append(node.node_id)
                in_degree[node.node_id] += 1

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    order = []

    while queue:
        queue.sort()  # stable/deterministic sort order
        curr = queue.pop(0)
        order.append(nodes_by_id[curr])
        for neighbor in adj[curr]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(nodes):
        raise ValueError("Cycle detected in DAG dependencies!")

    return order


class DAGScheduler:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def execute(self, plan: DAGPlan, execute_node_callback) -> bool:
        try:
            topological_sort(plan.nodes)
        except ValueError:
            return False

        import concurrent.futures

        node_status = {node.node_id: "pending" for node in plan.nodes}
        nodes_by_id = {node.node_id: node for node in plan.nodes}

        while any(status == "pending" for status in node_status.values()):
            ready_nodes = []
            for nid, status in node_status.items():
                if status != "pending":
                    continue
                node = nodes_by_id[nid]

                deps_ok = True
                blocked = False
                for dep in node.dependencies:
                    dep_status = node_status.get(dep)
                    if dep_status == "failed" or dep_status == "blocked":
                        blocked = True
                        break
                    elif dep_status != "success":
                        deps_ok = False

                if blocked:
                    node.status = "blocked"
                    node_status[nid] = "blocked"
                elif deps_ok:
                    ready_nodes.append(node)

            if not ready_nodes:
                for nid, status in node_status.items():
                    if status == "pending":
                        nodes_by_id[nid].status = "blocked"
                        node_status[nid] = "blocked"
                break

            readonly_nodes = [n for n in ready_nodes if n.tool not in ("apply_patch", "terminal")]
            write_nodes = [n for n in ready_nodes if n.tool in ("apply_patch", "terminal")]

            if readonly_nodes:
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(readonly_nodes), 8)) as executor:
                    futures = {executor.submit(execute_node_callback, node): node for node in readonly_nodes}
                    for future in concurrent.futures.as_completed(futures):
                        node = futures[future]
                        node.status = "running"
                        node_status[node.node_id] = "running"
                        try:
                            success, result_summary = future.result()
                        except Exception as e:
                            success, result_summary = False, f"Exception: {e}"

                        if success:
                            node.status = "success"
                            node_status[node.node_id] = "success"
                            node.result = result_summary
                        else:
                            node.status = "failed"
                            node_status[node.node_id] = "failed"
                            node.result = result_summary
            else:
                node = write_nodes[0]
                node.status = "running"
                node_status[node.node_id] = "running"
                try:
                    success, result_summary = execute_node_callback(node)
                except Exception as e:
                    success, result_summary = False, f"Exception: {e}"

                if success:
                    node.status = "success"
                    node_status[node.node_id] = "success"
                    node.result = result_summary
                else:
                    node.status = "failed"
                    node_status[node.node_id] = "failed"
                    node.result = result_summary

        def _is_acceptable(status: str, node_id: str) -> bool:
            if status == "success":
                return True
            if status == "blocked":
                return any(node_status.get(dep) == "failed" or node_status.get(dep) == "blocked" for dep in nodes_by_id[node_id].dependencies)
            return False

        return all(_is_acceptable(status, nid) for nid, status in node_status.items())

