#!/usr/bin/env python3
"""Ask the local model for one module and one test. Keep both only on pass.

Not used by the goal cycle. Unknown implement goals stay a noop in the
change generator.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
BUDGET_CHARS = (21299 - 1536) * 3
MAX_LINES = 80
BANNED_CALLS = frozenset({"eval", "exec", "open", "compile", "__import__", "input"})
CLAMP_CASES = (
    (10, 0, 5, 5),
    (-1, 0, 5, 0),
    (3, 0, 5, 3),
    (0, 0, 5, 0),
    (5, 0, 5, 5),
)


def _load(filename: str, module_name: str):
    path = _HERE / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def module_import_name(module_rel: str) -> str:
    """src/labapp/clamp.py -> labapp.clamp."""
    rel = module_rel
    if rel.startswith("src/"):
        rel = rel[4:]
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def source_root(root: Path, module_rel: str) -> Path:
    if module_rel.startswith("src/"):
        return root / "src"
    return root


def _norm_rel(value: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    rel = value.strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    parts = rel.split("/")
    if (
        not rel
        or rel.startswith("/")
        or ".." in parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        return None
    return rel


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_json_object(text: str):
    text = _strip_fences(text or "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _imports_of(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append(".")
            else:
                found.append(node.module or "")
    return found


def _banned_calls(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALLS:
                found.append(node.func.id)
    return found


def check_source(rel: str, content: str, *, module_rel: str, test_rel: str) -> str | None:
    """Return a reason string, or None when the file is acceptable."""
    if rel not in {module_rel, test_rel}:
        return "unexpected_path"
    if not isinstance(content, str) or not content.strip():
        return "empty"
    lines = content.count("\n") + (0 if content.endswith("\n") else 1)
    if lines > MAX_LINES:
        return f"too_long:{lines}"
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        return f"syntax:{exc.msg}"
    calls = _banned_calls(tree)
    if calls:
        return f"banned_call:{calls[0]}"
    imports = _imports_of(tree)
    import_name = module_import_name(module_rel)
    stem = Path(module_rel).stem
    if rel == module_rel:
        allowed = {"typing"}
        if f"def {stem}" not in content:
            return "missing_function"
    else:
        allowed = {"unittest", import_name}
        if import_name not in imports:
            return "missing_module_import"
    extra = [name for name in imports if name not in allowed]
    if extra:
        return f"import:{extra[0]}"
    return None


def _take_files(text: str, module_rel: str, test_rel: str):
    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict):
        return None, "json_parse_failed"
    files = parsed.get("files")
    if not isinstance(files, list) or len(files) != 2:
        return None, "file_count"
    out = {}
    for entry in files:
        if not isinstance(entry, dict):
            return None, "file_entry"
        rel = entry.get("file")
        content = entry.get("content")
        if not isinstance(rel, str) or rel not in {module_rel, test_rel}:
            return None, f"path:{rel}"
        if not isinstance(content, str):
            return None, "content_type"
        out[rel] = content
    if set(out) != {module_rel, test_rel}:
        return None, "missing_path"
    return out, "ok"


def _prompts(module_rel: str, test_rel: str, task: str) -> tuple[str, str]:
    stem = Path(module_rel).stem
    import_name = module_import_name(module_rel)
    system = (
        "You write one Python module for ANU. Do not call tools. "
        "Reply with one JSON object only, no markdown. "
        'Schema: {"files":[{"file":"' + module_rel + '","content":"..."},'
        '{"file":"' + test_rel + '","content":"..."}]}. '
        "Exactly those two paths. Each content value is the full file."
    )
    user = (
        task.strip()
        + f"\nThe module file is {module_rel}. The test file is {test_rel}. "
        + f"Define {stem}. The test must import {import_name} and use unittest. "
        + "Keep each file under 40 lines."
    )
    return system, user


def _default_chat(prompt: str, system: str, timeout: float) -> dict:
    client = _load("anu-model-client-v0.1.py", "anu_model_client_local_module")
    return client.call_gateway(
        "chat",
        timeout=float(timeout),
        prompt=prompt,
        system=system,
        tool_choice="none",
    )


def _child_env(pythonpath: Path) -> dict:
    env = {
        "PYTHONPATH": str(pythonpath),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if os.name == "nt":
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT"):
            if key in os.environ:
                env[key] = os.environ[key]
    return env


def _run_unittest(root: Path, test_rel: str, pythonpath: Path) -> subprocess.CompletedProcess:
    test_path = root / test_rel
    return subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(test_path.parent),
            "-p",
            test_path.name,
        ],
        cwd=str(root),
        env=_child_env(pythonpath),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _delete(root: Path, rels: list[str]) -> None:
    for rel in rels:
        path = root / rel
        if path.is_file():
            path.unlink()


def _write(root: Path, files: dict[str, str]) -> list[str]:
    written = []
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def clamp_oracle(root: Path) -> tuple[bool, str]:
    """Fixed checks for the clamp lab contract. Does not trust the model test."""
    src = source_root(Path(root), "src/labapp/clamp.py")
    sys.path.insert(0, str(src))
    for name in list(sys.modules):
        if name == "labapp" or name.startswith("labapp."):
            del sys.modules[name]
    try:
        from labapp.clamp import clamp
    except Exception as exc:
        return False, f"oracle_import:{type(exc).__name__}"
    for value, low, high, expected in CLAMP_CASES:
        got = clamp(value, low, high)
        if got != expected:
            return False, f"oracle:{value},{low},{high}->{got}!={expected}"
    return True, "ok"


def propose_module(
    root,
    module_rel: str,
    test_rel: str,
    task: str,
    *,
    chat=None,
    oracle=None,
    timeout_seconds: float = 180,
) -> dict:
    """Validate a model reply and keep the two files only when tests pass."""
    root = Path(root)
    module_key = _norm_rel(module_rel)
    test_key = _norm_rel(test_rel)
    report = {
        "ok": False,
        "kept": False,
        "files": [],
        "reason": "bad_path",
        "attempts": 0,
        "prompt_chars": 0,
        "budget_chars": BUDGET_CHARS,
        "prompt_inside_budget": False,
        "model": None,
        "zero_cost": None,
        "model_test_exit": None,
        "oracle_ok": None,
        "oracle_reason": None,
    }
    if module_key is None or test_key is None or module_key == test_key:
        return report
    if not isinstance(task, str) or not task.strip():
        report["reason"] = "empty_task"
        return report

    system, user = _prompts(module_key, test_key, task)
    report["prompt_chars"] = len(system) + len(user)
    report["prompt_inside_budget"] = report["prompt_chars"] <= BUDGET_CHARS
    if not report["prompt_inside_budget"]:
        report["reason"] = "prompt_over_budget"
        return report

    chat_fn = chat or _default_chat
    prompt = user
    files = None
    reason = "not_run"
    last_chat: dict = {}
    for attempt in (1, 2):
        report["attempts"] = attempt
        try:
            last_chat = chat_fn(prompt, system, float(timeout_seconds))
        except Exception as exc:
            reason = f"chat_error:{type(exc).__name__}"
            prompt = user + (
                f"\n\nPrevious reply was invalid ({reason}). "
                "Return the JSON object only."
            )
            continue
        if not isinstance(last_chat, dict) or not last_chat.get("ok"):
            reason = "chat_not_ok"
            prompt = user + (
                "\n\nPrevious reply was invalid (chat_not_ok). "
                "Return the JSON object only."
            )
            continue
        files, reason = _take_files(last_chat.get("text") or "", module_key, test_key)
        if files is None:
            prompt = user + (
                f"\n\nPrevious reply was invalid ({reason}). "
                "Return the JSON object only."
            )
            continue
        problem = None
        for rel, content in files.items():
            problem = check_source(
                rel, content, module_rel=module_key, test_rel=test_key
            )
            if problem:
                problem = f"{rel}:{problem}"
                break
        if problem:
            reason = problem
            files = None
            prompt = user + (
                f"\n\nPrevious reply was invalid ({reason}). "
                "Return the JSON object only."
            )
            continue
        try:
            generator = _load(
                "anu-change-generator-v0.1.py",
                "anu_change_generator_local_module",
            )
            generator.validate_changes(
                [
                    {"file": rel, "old": "# stub\n", "new": content}
                    for rel, content in files.items()
                ],
                project_root=root,
            )
        except ValueError as exc:
            reason = f"changes:{exc}"
            files = None
            prompt = user + (
                "\n\nPrevious reply failed path checks. "
                "Return the JSON object only."
            )
            continue
        reason = "ok"
        break

    report["reason"] = reason
    report["model"] = last_chat.get("model") if isinstance(last_chat, dict) else None
    report["zero_cost"] = (
        last_chat.get("zero_cost") if isinstance(last_chat, dict) else None
    )
    if files is None:
        return report

    written = _write(root, files)
    try:
        completed = _run_unittest(root, test_key, source_root(root, module_key))
    except subprocess.TimeoutExpired:
        _delete(root, written)
        report["reason"] = "test_timeout"
        report["model_test_exit"] = None
        return report
    report["model_test_exit"] = completed.returncode
    if completed.returncode != 0:
        _delete(root, written)
        report["reason"] = "test_failed"
        if completed.stderr:
            report["test_stderr"] = completed.stderr[-500:]
        return report

    if oracle is not None:
        try:
            oracle_ok, oracle_reason = oracle(root)
        except Exception as exc:
            oracle_ok, oracle_reason = False, f"oracle_error:{type(exc).__name__}"
        report["oracle_ok"] = bool(oracle_ok)
        report["oracle_reason"] = oracle_reason
        if not oracle_ok:
            _delete(root, written)
            report["reason"] = oracle_reason or "oracle_failed"
            return report

    report["ok"] = True
    report["kept"] = True
    report["files"] = written
    report["reason"] = "ok"
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ANU local module writer v0.1")
    parser.add_argument("--root", required=True)
    parser.add_argument("--module", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--oracle",
        choices=("clamp",),
        default=None,
        help="Optional fixed checker run after the model test passes",
    )
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    oracle = clamp_oracle if args.oracle == "clamp" else None
    result = propose_module(
        args.root,
        args.module,
        args.test,
        args.task,
        oracle=oracle,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(result, indent=2))
    return 0 if result.get("kept") else 1


if __name__ == "__main__":
    raise SystemExit(main())
