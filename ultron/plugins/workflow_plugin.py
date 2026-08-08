"""
workflow_plugin.py — Ultron Workflow Engine ⭐⭐⭐⭐⭐

Save and replay multi-step command sequences with a single command.

Tools:
  1. create_workflow  → Save a named workflow with a list of action steps
  2. run_workflow     → Execute all steps of a saved workflow sequentially
  3. list_workflows   → List all saved workflows
  4. delete_workflow  → Delete a saved workflow by name
"""

import os
import json
import datetime
import time


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _get_workflows_path() -> str:
    """Returns the absolute path to the workflows.json file."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_dir, "data", "workflows.json")


def _load_workflows() -> dict:
    """Loads all workflows from disk."""
    path = _get_workflows_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return {}


def _save_workflows(data: dict):
    """Persists workflows to disk."""
    path = _get_workflows_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# 1. Create Workflow
# ---------------------------------------------------------------------------

def create_workflow(name: str, steps: str) -> str:
    """Saves a named workflow with a sequence of action steps.
    The workflow can later be executed with run_workflow.
    Args:
        name: A human-readable name for the workflow (e.g. 'RateUp Development Setup').
        steps: A JSON array string of step commands. Each step is in the format 'tool_name arg1 arg2'.
               Example: '["open_application vscode", "open_application docker", "browser_navigate localhost:3000"]'
    """
    try:
        # Parse the steps JSON array
        try:
            step_list = json.loads(steps)
        except json.JSONDecodeError:
            # Fallback: try splitting by comma if not valid JSON
            step_list = [s.strip().strip('"').strip("'") for s in steps.split(",") if s.strip()]

        if not step_list or not isinstance(step_list, list):
            return "Error: Steps must be a non-empty list of action strings."

        workflows = _load_workflows()
        workflows[name] = {
            "steps": step_list,
            "created": datetime.datetime.now().isoformat(),
            "step_count": len(step_list)
        }
        _save_workflows(workflows)

        step_preview = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(step_list))
        return (
            f"Workflow '{name}' saved successfully with {len(step_list)} steps:\n"
            f"{step_preview}"
        )
    except Exception as e:
        return f"Failed to create workflow: {e}"


# ---------------------------------------------------------------------------
# 2. Run Workflow
# ---------------------------------------------------------------------------

def run_workflow(name: str, tool_functions: dict = None) -> str:
    """Executes all steps of a saved workflow sequentially.
    Args:
        name: The name of the workflow to run.
        tool_functions: Dictionary of available tool functions (injected by Brain).
    """
    try:
        workflows = _load_workflows()

        # Fuzzy match: case-insensitive lookup
        matched_name = None
        for wf_name in workflows:
            if wf_name.lower() == name.lower():
                matched_name = wf_name
                break

        if not matched_name:
            # Try partial match
            for wf_name in workflows:
                if name.lower() in wf_name.lower() or wf_name.lower() in name.lower():
                    matched_name = wf_name
                    break

        if not matched_name:
            available = ", ".join(workflows.keys()) if workflows else "none"
            return f"Workflow '{name}' not found. Available workflows: {available}"

        workflow = workflows[matched_name]
        steps = workflow.get("steps", [])

        if not steps:
            return f"Workflow '{matched_name}' has no steps to execute."

        if not tool_functions:
            return f"Workflow '{matched_name}' cannot be executed: tool functions not available."

        results = []
        results.append(f"Executing workflow: '{matched_name}' ({len(steps)} steps)\n")

        for i, step in enumerate(steps, 1):
            # Parse step: "tool_name arg1 arg2 ..."
            parts = step.strip().split(maxsplit=1)
            tool_name = parts[0]
            raw_args = parts[1] if len(parts) > 1 else ""

            if tool_name not in tool_functions:
                results.append(f"  Step {i}: SKIPPED — Unknown tool '{tool_name}'")
                continue

            func = tool_functions[tool_name]

            try:
                # Determine how many arguments the function expects
                import inspect
                sig = inspect.signature(func)
                params = list(sig.parameters.values())

                if not params or not raw_args:
                    # No-arg function
                    result = func()
                elif len(params) == 1:
                    # Single-arg function
                    result = func(raw_args)
                else:
                    # Multi-arg: try to split by pipe delimiter or spaces
                    if "|" in raw_args:
                        arg_list = [a.strip() for a in raw_args.split("|")]
                    else:
                        # Split respecting the number of expected params
                        arg_list = raw_args.split(maxsplit=len(params) - 1)

                    result = func(*arg_list[:len(params)])

                results.append(f"  Step {i}: {step} → {result}")
            except Exception as e:
                results.append(f"  Step {i}: {step} → Error: {e}")

            # Small delay between steps to let apps launch
            if i < len(steps):
                time.sleep(2)

        results.append(f"\nWorkflow '{matched_name}' completed.")
        return "\n".join(results)

    except Exception as e:
        return f"Failed to run workflow: {e}"


# ---------------------------------------------------------------------------
# 3. List Workflows
# ---------------------------------------------------------------------------

def list_workflows() -> str:
    """Lists all saved workflows with their names, step counts, and creation dates.
    Call this when the user asks to see their workflows.
    """
    try:
        workflows = _load_workflows()

        if not workflows:
            return "No workflows saved yet."

        lines = [f"Saved Workflows ({len(workflows)} total):\n"]
        for i, (name, data) in enumerate(workflows.items(), 1):
            step_count = data.get("step_count", len(data.get("steps", [])))
            created = data.get("created", "Unknown")
            steps_preview = ", ".join(data.get("steps", [])[:3])
            if len(data.get("steps", [])) > 3:
                steps_preview += ", ..."
            lines.append(
                f"{i}. {name}  [{step_count} steps] (Created: {created})\n"
                f"   Steps: {steps_preview}"
            )

        return "\n".join(lines)

    except Exception as e:
        return f"Failed to list workflows: {e}"


# ---------------------------------------------------------------------------
# 4. Delete Workflow
# ---------------------------------------------------------------------------

def delete_workflow(name: str) -> str:
    """Deletes a saved workflow by name.
    Args:
        name: The name of the workflow to delete.
    """
    try:
        workflows = _load_workflows()

        # Fuzzy match: case-insensitive lookup
        matched_name = None
        for wf_name in workflows:
            if wf_name.lower() == name.lower():
                matched_name = wf_name
                break

        if not matched_name:
            for wf_name in workflows:
                if name.lower() in wf_name.lower() or wf_name.lower() in name.lower():
                    matched_name = wf_name
                    break

        if not matched_name:
            available = ", ".join(workflows.keys()) if workflows else "none"
            return f"Workflow '{name}' not found. Available workflows: {available}"

        del workflows[matched_name]
        _save_workflows(workflows)
        return f"Workflow '{matched_name}' has been deleted."

    except Exception as e:
        return f"Failed to delete workflow: {e}"
