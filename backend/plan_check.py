import logging

from references import find_refs


logger = logging.getLogger("fatmee.plan_check")


class PlanError(ValueError):
    """The plan cannot be run as written (e.g. steps wait on each other)."""


def check_plan(steps: list) -> tuple[list, list]:
    """
    Repair small mistakes in a plan and report what was changed.

    - A dependency written as a step number ("1") is mapped to that
      step's id ("s1").
    - Unknown dependencies and steps waiting on themselves are dropped.

    Returns (fixed_steps, warnings). Raises PlanError when steps wait
    on each other in a loop, since no order can satisfy that.
    """

    ids = {step["id"] for step in steps}
    by_number = {str(step.get("step")): step["id"] for step in steps}

    warnings = []
    fixed = []

    for step in steps:
        deps = []

        for dep in step.get("depends_on", []):
            dep = str(dep)

            if dep not in ids and dep in by_number:
                dep = by_number[dep]

            if dep == step["id"]:
                warnings.append(f"{step['id']} depended on itself; ignored")
                continue

            if dep not in ids:
                warnings.append(f"{step['id']} depended on unknown step {dep!r}; ignored")
                continue

            if dep not in deps:
                deps.append(dep)

        # Using "$s2" in args means waiting for s2.
        for ref in sorted(find_refs(step.get("args") or {}) & ids):
            if ref != step["id"] and ref not in deps:
                deps.append(ref)
                warnings.append(f"{step['id']} uses ${ref}; added it to depends_on")

        fixed.append({**step, "depends_on": deps})

    # Raises PlanError on a loop.
    execution_waves(fixed)

    for warning in warnings:
        logger.warning("Plan repaired: %s", warning)

    return fixed, warnings


def execution_waves(steps: list) -> list:
    """
    Group steps into waves that can run at the same time.

    Wave 1 holds steps that wait for nothing; each later wave holds
    steps whose dependencies all finished in earlier waves.
    """

    # Dicts keep insertion order, so each wave keeps the planner's order.
    remaining = {step["id"]: step for step in steps}
    done = set()
    waves = []

    while remaining:
        ready = [
            step for step in remaining.values()
            if all(dep in done for dep in step.get("depends_on", []))
        ]

        if not ready:
            loop = sorted(remaining)
            raise PlanError(f"Steps wait on each other in a loop: {loop}")

        waves.append(ready)

        for step in ready:
            done.add(step["id"])
            del remaining[step["id"]]

    return waves
