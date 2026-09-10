class GoalVerifier:
    """
    Verifies whether the available tool results are sufficient
    to satisfy the user's goal.
    """

    def verify(self, goal: str, plan: dict, tool_results: dict) -> dict:

        # No tools required
        if not tool_results:
            return {
                "goal_satisfied": True,
                "reason": "No tools required; goal can be handled conversationally.",
                "missing_information": []
            }

        goal_lower = goal.lower()

        # ====================================================
        # LOCAL LLM / HARDWARE ASSESSMENT
        # ====================================================

        if (
            "7b" in goal_lower
            or "local llm" in goal_lower
            or "local model" in goal_lower
        ):

            hardware = tool_results.get("get_hardware_info")
            ram = tool_results.get("get_ram_usage")

            missing = []

            # Hardware information
            if not hardware:
                missing.append("hardware information")

            else:

                if hardware.get("ram_gb") is None:
                    missing.append("total RAM")

                if hardware.get("cpu") is None:
                    missing.append("CPU information")

                # GPU and VRAM are OPTIONAL.
                # They are useful for performance assessment,
                # but not required to determine basic CPU-based
                # local LLM feasibility.

            # Current RAM information
            if not ram:
                missing.append("current RAM usage")

            else:

                if ram.get("available_gb") is None:
                    missing.append("available RAM")

            # =================================================
            # RESULT
            # =================================================

            if missing:

                return {
                    "goal_satisfied": False,
                    "reason": (
                        "The available CPU and memory information "
                        "is not sufficient for a basic local LLM assessment."
                    ),
                    "missing_information": missing
                }

            return {
                "goal_satisfied": True,
                "reason": (
                    "CPU and memory information is sufficient for "
                    "a basic local LLM feasibility assessment. "
                    "GPU and VRAM can be used to assess acceleration "
                    "and performance."
                ),
                "missing_information": []
            }

        # ====================================================
        # GENERIC GOAL
        # ====================================================

        return {
            "goal_satisfied": True,
            "reason": "Tool results are available for analysis.",
            "missing_information": []
        }