from executor import result_keys, tool_steps


class Verifier:
    """
    Verifies whether planned tool execution produced valid results.
    """

    def verify(self, plan: dict, tool_results: dict) -> dict:

        expected = result_keys(tool_steps(plan))

        errors = []

        # Check whether every planned tool executed
        missing_tools = [key for key in expected if key not in tool_results]

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

            elif isinstance(result, dict) and result.get("rejected"):
                # The user said no: that is a decision, not a failure
                # to recover from.
                continue

            elif isinstance(result, dict) and "error" in result:
                errors.append(
                    f"{tool_name} failed: {result['error']}"
                )

        return {
            "verified": len(errors) == 0,
            "errors": errors
        }
