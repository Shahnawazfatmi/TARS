import inspect

from executor import execute_plan, result_keys, tool_steps
from tools.registry import TOOLS, run_tool
from verifier import Verifier


def test_every_tool_runs_without_error():
    for tool in TOOLS:
        result = tool.func()
        assert result is not None, tool.name


def test_declared_parameters_exist_on_function():
    for tool in TOOLS:
        accepted = inspect.signature(tool.func).parameters
        for param in tool.parameters:
            assert param in accepted, f"{tool.name}.{param}"


def test_unknown_tool_and_bad_args():
    assert "error" in run_tool("does_not_exist")
    # Unknown argument is ignored instead of crashing
    assert isinstance(run_tool("get_ram_usage", {"bogus": 1}), dict)


def test_list_files_missing_folder():
    assert "error" in run_tool("list_files", {"path": "Z:/no/such/folder"})


def test_hardware_info_is_cached_but_copied():
    first = run_tool("get_hardware_info")
    first["gpus"].append("tampered")
    second = run_tool("get_hardware_info")
    assert "tampered" not in second["gpus"]
    assert second["ram_gb"] > 0


def test_duplicate_steps_removed_and_repeat_tools_keyed():
    plan = {"steps": [
        {"tool": "list_files", "args": {"path": "."}},
        {"tool": "list_files", "args": {"path": "."}},
        {"tool": "list_files", "args": {"path": ".."}},
        {"tool": None},
    ]}
    steps = tool_steps(plan)
    assert len(steps) == 2
    assert result_keys(steps) == ["list_files", "list_files#2"]


def test_execute_and_verify():
    plan = {"steps": [{"tool": "get_ram_usage"}, {"tool": "get_cpu_usage"}]}
    results = execute_plan(plan)
    assert set(results) == {"get_ram_usage", "get_cpu_usage"}
    assert Verifier().verify(plan, results)["verified"]


def test_verifier_flags_errors_and_missing():
    plan = {"steps": [{"tool": "get_ram_usage"}, {"tool": "nope"}]}
    results = execute_plan(plan)
    check = Verifier().verify(plan, results)
    assert not check["verified"]
    assert any("nope" in e for e in check["errors"])


def test_top_processes_fast_and_well_formed():
    import time

    start = time.perf_counter()
    rows = run_tool("get_top_processes", {"sort_by": "cpu", "limit": 5})
    assert time.perf_counter() - start < 2
    assert len(rows) == 5
    assert all({"pid", "name", "memory_mb", "cpu_percent"} <= set(r) for r in rows)
    assert all(r["pid"] != 0 for r in rows)

    by_mem = run_tool("get_top_processes", {"sort_by": "memory", "limit": 3})
    assert by_mem[0]["memory_mb"] >= by_mem[-1]["memory_mb"] > 0


def test_installed_programs_similar_matches(monkeypatch):
    from tools import system_tools

    if system_tools.winreg is None:
        return

    monkeypatch.setattr(system_tools, "_read_installed_programs", lambda: {
        "WPS Office (12.1)": "12.1",
        "Microsoft Visual C++ 2022": "14.0",
        "Python 3.12.10 (64-bit)": "3.12.10",
    })

    office = run_tool("get_installed_programs", {"name_filter": "Microsoft Office"})
    assert office["total"] == 0
    # "microsoft" alone is too generic to match Visual C++
    assert [p["name"] for p in office["similar"]] == ["WPS Office (12.1)"]

    python = run_tool("get_installed_programs", {"name_filter": "python"})
    assert python["total"] == 1 and "similar" not in python
