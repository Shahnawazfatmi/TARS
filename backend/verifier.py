class Verifier:
    """
    Verifies whether planned tool execution produced valid results.
    """

    def verify(self, plan: dict, tool_results: dict) -> dict:

        planned_tools = [
            step["tool"]
            for step in plan.get("steps", [])
            if step.get("tool")
        ]

        errors = []
        missing_tools = []

        # Check whether every planned tool executed
        for tool in planned_tools:
            if tool not in tool_results:
                missing_tools.append(tool)

        if missing_tools:
            errors.append(
                f"Planned tools were not executed: {missing_tools}"
            )

        # Check tool results
        for tool_name, result in tool_results.items():

            if result is None:
                errors.append(
                    f"{tool_name} returned no result."
                )

            elif isinstance(result, dict) and "error" in result:
                errors.append(
                    f"{tool_name} failed: {result['error']}"
                )

        return {
            "verified": len(errors) == 0,
            "errors": errors
        }