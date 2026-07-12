#!/usr/bin/env python3
"""Contract and regression tests for the portable Windows prompt hook."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "run-powershell-safely"
HOOK_PATH = PLUGIN_ROOT / "hooks" / "windows_prompt_route.py"
HOOK_CONFIG_PATH = PLUGIN_ROOT / "hooks" / "hooks.json"
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
MARKETPLACE_PATH = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "windows_prompt_cases.json"
CANONICAL_SKILL = REPO_ROOT / "skills" / "run-powershell-safely"
PLUGIN_SKILL = PLUGIN_ROOT / "skills" / "run-powershell-safely"
SYNC_SCRIPT = REPO_ROOT / "tests" / "sync_plugin_skill.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("windows_prompt_route", HOOK_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {HOOK_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_hook(
    payload: object | None = None,
    *,
    raw: str | None = None,
    plugin_root: Path = PLUGIN_ROOT,
    extra_env: dict[str, str] | None = None,
):
    stdin = raw if raw is not None else json.dumps(payload)
    env = {"PLUGIN_ROOT": str(plugin_root)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
        env=env,
    )


class WindowsPromptRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_hook_module()
        cls.fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.plugin_skill_text = (PLUGIN_SKILL / "SKILL.md").read_text(
            encoding="utf-8"
        )

    def test_positive_and_old_failure_fixtures_get_same_turn_skill(self) -> None:
        exact_skill_path = str((PLUGIN_SKILL / "SKILL.md").resolve())
        for group in ("positive", "old_failure"):
            for case in self.fixtures[group]:
                with self.subTest(group=group, case=case["id"]):
                    self.assertTrue(self.module.classify_prompt(case["prompt"]))
                    result = _run_hook({"prompt": case["prompt"]})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    output = payload["hookSpecificOutput"]
                    self.assertEqual(output["hookEventName"], "UserPromptSubmit")
                    context = output["additionalContext"]
                    self.assertIn(exact_skill_path, context)
                    self.assertIn("same-turn bundled context", context)
                    self.assertIn("BEGIN PLUGIN-OWNED SKILL.md", context)
                    self.assertIn(self.plugin_skill_text.rstrip(), context)
                    self.assertIn("END PLUGIN-OWNED SKILL.md", context)
                    self.assertLessEqual(
                        len(context.encode("utf-8")),
                        self.module.MAX_SKILL_BYTES + 4096,
                    )

    def test_negative_fixtures_are_true_noops(self) -> None:
        for case in self.fixtures["negative"]:
            with self.subTest(case=case["id"]):
                self.assertFalse(self.module.classify_prompt(case["prompt"]))
                result = _run_hook({"prompt": case["prompt"]})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_empty_unknown_and_malformed_inputs_are_noops(self) -> None:
        for raw in ("", "not-json", "[]", '{"unrelated":"PowerShell"}'):
            with self.subTest(raw=raw):
                result = _run_hook(raw=raw)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_macos_windows_ssh_boundary_is_explicit(self) -> None:
        result = _run_hook(
            {"prompt": "On macOS, use SSH to run PowerShell on a Windows host."}
        )
        output = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("macOS", output)
        self.assertIn("Windows-over-SSH", output)
        self.assertIn("windows_command.py", output)
        self.assertIn("do not use", output)

    def test_hook_config_uses_portable_plugin_root_and_default_discovery(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        groups = config["hooks"]["UserPromptSubmit"]
        self.assertEqual(len(groups), 1)
        self.assertNotIn("matcher", groups[0])
        handlers = groups[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]
        self.assertIn("$PLUGIN_ROOT/hooks/windows_prompt_route.py", handler["command"])
        self.assertIn(
            "%PLUGIN_ROOT%\\hooks\\windows_prompt_route.py",
            handler["commandWindows"],
        )
        self.assertLessEqual(handler["timeout"], 5)

        manifest = json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.0.2")
        self.assertNotIn("hooks", manifest)
        self.assertEqual(manifest["skills"], "./skills/")

    def test_repo_marketplace_points_to_clean_plugin_subtree(self) -> None:
        marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(marketplace["plugins"]), 1)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "run-powershell-safely")
        self.assertEqual(entry["source"]["source"], "local")
        source_path = entry["source"]["path"]
        self.assertEqual(source_path, "./plugins/run-powershell-safely")
        resolved_plugin = (REPO_ROOT / source_path).resolve()
        self.assertEqual(resolved_plugin, PLUGIN_ROOT.resolve())
        self.assertTrue((resolved_plugin / ".codex-plugin" / "plugin.json").is_file())
        self.assertFalse((REPO_ROOT / ".codex-plugin" / "plugin.json").exists())

    def test_plugin_subtree_excludes_repository_and_runtime_state(self) -> None:
        for forbidden in (".git", ".ai-controller", ".tldr", "__pycache__"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse((PLUGIN_ROOT / forbidden).exists())
                self.assertNotIn(forbidden, {path.name for path in PLUGIN_ROOT.rglob("*")})
        self.assertEqual(list(PLUGIN_ROOT.rglob("*.pyc")), [])

    def test_plugin_skill_mirror_matches_canonical_standalone_skill(self) -> None:
        self.assertTrue((CANONICAL_SKILL / "SKILL.md").is_file())
        self.assertTrue((PLUGIN_SKILL / "SKILL.md").is_file())
        result = subprocess.run(
            [sys.executable, str(SYNC_SCRIPT), "--check"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("plugin skill mirror matches", result.stdout)

    def test_same_named_standalone_skill_cannot_override_plugin_context(self) -> None:
        case = self.fixtures["coexistence"][0]
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            plugin_root = base / "plugin"
            plugin_skill = plugin_root / "skills" / "run-powershell-safely" / "SKILL.md"
            plugin_skill.parent.mkdir(parents=True)
            plugin_skill.write_text(case["plugin_content"], encoding="utf-8")

            codex_home = base / "codex-home"
            standalone_skill = (
                codex_home / "skills" / "run-powershell-safely" / "SKILL.md"
            )
            standalone_skill.parent.mkdir(parents=True)
            standalone_skill.write_text(case["standalone_content"], encoding="utf-8")

            result = _run_hook(
                {"prompt": case["prompt"]},
                plugin_root=plugin_root,
                extra_env={"CODEX_HOME": str(codex_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            self.assertIn(str(plugin_skill.resolve()), context)
            self.assertIn(case["plugin_content"].rstrip(), context)
            self.assertNotIn(str(standalone_skill.resolve()), context)
            self.assertNotIn(case["standalone_content"], context)

    def test_unsafe_or_oversized_skill_content_fails_closed_without_leak(self) -> None:
        cases = (
            (
                "private-contact" + "@" + "example.com",
                "skill_privacy_check_failed",
            ),
            ("X" * (self.module.MAX_SKILL_BYTES + 1), "skill_oversize"),
        )
        for content, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with tempfile.TemporaryDirectory() as temporary:
                    plugin_root = Path(temporary) / "plugin"
                    skill = (
                        plugin_root
                        / "skills"
                        / "run-powershell-safely"
                        / "SKILL.md"
                    )
                    skill.parent.mkdir(parents=True)
                    skill.write_text(content, encoding="utf-8")
                    result = _run_hook(
                        {"prompt": "Run PowerShell on Windows."},
                        plugin_root=plugin_root,
                    )
                    context = json.loads(result.stdout)["hookSpecificOutput"][
                        "additionalContext"
                    ]
                    self.assertIn("WINDOWS COMMAND ROUTE BLOCKED", context)
                    self.assertIn(expected_error, context)
                    self.assertNotIn(content, context)

    def test_public_hook_has_no_command_execution_or_private_identifiers(self) -> None:
        source = HOOK_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "os.system",
            "shell=True",
            "PasswordAuthentication",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
