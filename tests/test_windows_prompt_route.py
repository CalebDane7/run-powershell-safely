#!/usr/bin/env python3
"""Contract and regression tests for the portable Windows prompt hook."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "run-powershell-safely"
HOOK_PATH = PLUGIN_ROOT / "hooks" / "windows_prompt_route.py"
DISPATCH_PATH = PLUGIN_ROOT / "hooks" / "version_resilient_dispatch.py"
HOOK_CONFIG_PATH = PLUGIN_ROOT / "hooks" / "hooks.json"
PLUGIN_MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
PACKAGE_VALIDATOR_PATH = PLUGIN_ROOT / "scripts" / "validate_package.py"
COMPATIBILITY_WORKFLOW_PATH = (
    REPO_ROOT / ".github" / "workflows" / "codex-latest-compatibility.yml"
)
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


def _run_loaded_posix_command(
    command: str,
    payload: object | None = None,
    *,
    raw: str | None = None,
    plugin_root: Path,
):
    """Execute the exact command text a live POSIX Codex session retained."""

    stdin = raw if raw is not None else json.dumps(payload)
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(
        ["/bin/sh", "-c", command],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
        env=env,
    )


def _assert_nonblocking_hook_payload(test: unittest.TestCase, raw: str) -> dict:
    payload = json.loads(raw)
    test.assertEqual(set(payload), {"hookSpecificOutput"})
    output = payload["hookSpecificOutput"]
    test.assertEqual(
        set(output),
        {"hookEventName", "additionalContext"},
    )
    test.assertEqual(output["hookEventName"], "UserPromptSubmit")
    test.assertIsInstance(output["additionalContext"], str)
    return output


def _set_manifest_version(plugin_root: Path, version: str) -> None:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
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
                    output = _assert_nonblocking_hook_payload(self, result.stdout)
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

    def test_invalid_or_missing_event_schema_fails_open_actionably(self) -> None:
        for raw in (
            "",
            "not-json",
            "[]",
            '{"unrelated":"PowerShell"}',
            '{"prompt":42}',
        ):
            with self.subTest(raw=raw):
                result = _run_hook(raw=raw)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                output = _assert_nonblocking_hook_payload(self, result.stdout)
                context = output["additionalContext"]
                self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", context)
                self.assertIn("Continue the user's task", context)
                self.assertIn("proceed normally", context)
                self.assertNotIn("ROUTE BLOCKED", context)
                if raw:
                    self.assertNotIn(raw, context)

    def test_latest_codex_compatibility_workflow_guards_local_install(self) -> None:
        workflow = COMPATIBILITY_WORKFLOW_PATH.read_text(encoding="utf-8")
        for required in (
            "schedule:",
            'cron: "23 7 * * *"',
            "workflow_dispatch:",
            "actions/checkout@v7",
            "actions/setup-node@v6",
            "actions/setup-python@v6",
            "@openai/codex@latest",
            'mkdir -p "$CODEX_HOME"',
            'codex plugin marketplace add "$GITHUB_WORKSPACE" --json',
            "codex plugin list --available --json",
            "codex plugin add run-powershell-safely@run-powershell-safely --json",
            "codex plugin list --json",
            "tests/mock_responses_server.py",
            "codex exec --ephemeral",
            "model_providers.route09.requires_openai_auth=false",
            "Run PowerShell Get-Process on this Windows host.",
            "Explain this Linux shell command.",
            "tests/assert_lifecycle_capture.py",
            "--expect active",
            "--expect silent",
            "notify-on-failure:",
            "issues: write",
            "GH_TOKEN: ${{ github.token }}",
            "needs.latest-codex-plugin-contract.result",
            "Codex latest compatibility check failed",
            "gh issue list",
            "gh issue create",
            "gh issue reopen",
            "gh issue close",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        # WHY: runner context is unavailable in job-level env. This exact
        # placement previously made GitHub reject the workflow with zero jobs,
        # which disabled the update monitor without running any test.
        before_steps = workflow.split("    steps:", 1)[0]
        self.assertNotIn("runner.temp", before_steps)
        for forbidden in (
            "actions/checkout@v4",
            "actions/setup-node@v4",
            "actions/setup-python@v5",
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "secrets.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)

    def test_macos_windows_ssh_boundary_is_explicit(self) -> None:
        result = _run_hook(
            {"prompt": "On macOS, use SSH to run PowerShell on a Windows host."}
        )
        output = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("macOS", output)
        self.assertIn("Windows-over-SSH", output)
        self.assertIn("windows_command.py", output)
        self.assertIn("do not use", output)

    def test_hook_config_uses_update_resilient_inline_bootstrap(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(set(config["hooks"]), {"UserPromptSubmit"})
        groups = config["hooks"]["UserPromptSubmit"]
        self.assertEqual(len(groups), 1)
        self.assertNotIn("matcher", groups[0])
        handlers = groups[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        handler = handlers[0]
        self.assertTrue(handler["command"].startswith("python3 -c "))
        self.assertTrue(handler["commandWindows"].startswith('python -c "'))
        self.assertIn("version_resilient_dispatch.py", handler["command"])
        self.assertIn("version_resilient_dispatch.py", handler["commandWindows"])
        self.assertIn("timeout=2", handler["command"])
        self.assertIn("|| printf", handler["command"])
        self.assertIn("|| echo", handler["commandWindows"])
        self.assertNotIn("exec(", handler["command"])
        self.assertNotIn("exec(", handler["commandWindows"])
        self.assertNotIn("$PLUGIN_ROOT/hooks/windows_prompt_route.py", handler["command"])
        self.assertNotIn("%PLUGIN_ROOT%", handler["commandWindows"])
        for text in (handler["command"], handler["commandWindows"]):
            self.assertNotIn("EncodedCommand", text)
            self.assertNotIn("base64", text.casefold())
        self.assertNotIn("%", handler["commandWindows"])
        self.assertNotIn("!", handler["commandWindows"])
        self.assertLessEqual(handler["timeout"], 5)
        self.assertLess(len(handler["commandWindows"]), 8191)

        manifest = json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "1.0.3")
        self.assertNotIn("hooks", manifest)
        self.assertEqual(manifest["skills"], "./skills/")

    def test_loaded_old_command_survives_deleted_original_version(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as temporary:
            versions = Path(temporary) / "cache" / "market" / "plugin"
            old_root = versions / "1.0.2"
            newest_root = versions / "1.0.10+codex.local-20260713"
            invalid_newer = versions / "99.0.0"
            shutil.copytree(PLUGIN_ROOT, old_root)
            shutil.copytree(PLUGIN_ROOT, newest_root)
            shutil.copytree(PLUGIN_ROOT, invalid_newer)
            _set_manifest_version(old_root, "1.0.2")
            _set_manifest_version(newest_root, "1.0.10")

            # WHY: this reproduces the exact Codex update regression: the live
            # session retains command text and an old PLUGIN_ROOT after the old
            # cache version has been removed.
            shutil.rmtree(old_root)
            result = _run_loaded_posix_command(
                command,
                {"prompt": "Use PowerShell to list Windows services."},
                plugin_root=old_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            self.assertIn(str(newest_root.resolve()), output["additionalContext"])
            self.assertNotIn(str(invalid_newer.resolve()), output["additionalContext"])

    def test_loaded_command_prefers_newest_cachebuster_while_original_exists(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as temporary:
            versions = Path(temporary) / "cache" / "market" / "plugin"
            original = versions / "1.0.3"
            stable_cachebuster = versions / "1.0.3+codex.local-20260712"
            older_prerelease = versions / "1.0.4-rc.2+codex.local-20260713"
            newest_prerelease = versions / "1.0.4-rc.10+codex.local-20260714"
            for root, version in (
                (original, "1.0.3"),
                (stable_cachebuster, "1.0.3"),
                (older_prerelease, "1.0.4-rc.2"),
                (newest_prerelease, "1.0.4-rc.10"),
            ):
                shutil.copytree(PLUGIN_ROOT, root)
                _set_manifest_version(root, version)

            result = _run_loaded_posix_command(
                command,
                {"prompt": "Use PowerShell to inspect Windows services."},
                plugin_root=original,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            self.assertIn(
                str(newest_prerelease.resolve()),
                output["additionalContext"],
            )
            self.assertNotIn(
                str(original.resolve()) + "/skills",
                output["additionalContext"],
            )

    def test_loaded_command_with_no_sibling_fails_open_actionably(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as temporary:
            missing_root = Path(temporary) / "cache" / "market" / "plugin" / "1.0.2"
            missing_root.parent.mkdir(parents=True)
            result = _run_loaded_posix_command(
                command,
                {"prompt": "Run Get-Service in Windows PowerShell."},
                plugin_root=missing_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            context = output["additionalContext"]
            self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", context)
            self.assertIn("Continue the current user task", context)
            self.assertIn("reinstall", context)
            self.assertNotIn("ROUTE BLOCKED", context)

    def test_loaded_command_with_no_sibling_keeps_unrelated_prompt_silent(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        prompts = (
            "Add a Linux unit test for the README parser.",
            "Make the README title searchable for Windows, PowerShell, WSL, and SSH.",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt), tempfile.TemporaryDirectory() as temporary:
                missing_root = (
                    Path(temporary) / "cache" / "market" / "plugin" / "1.0.2"
                )
                missing_root.parent.mkdir(parents=True)
                result = _run_loaded_posix_command(
                    command,
                    {"prompt": prompt},
                    plugin_root=missing_root,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

        with tempfile.TemporaryDirectory() as temporary:
            missing_root = Path(temporary) / "cache" / "market" / "plugin" / "1.0.2"
            missing_root.parent.mkdir(parents=True)
            result = _run_loaded_posix_command(
                command,
                raw="{malformed",
                plugin_root=missing_root,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", output["additionalContext"])
            self.assertIn("Continue the current user task", output["additionalContext"])

    def test_windows_command_embeds_the_same_fail_open_bootstrap(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0][
            "commandWindows"
        ]
        prefix = 'python -c "'
        self.assertTrue(command.startswith(prefix))
        suffix = '" 2>nul || echo '
        self.assertIn(suffix, command)
        python_source = command[len(prefix) : command.index(suffix)]

        with tempfile.TemporaryDirectory() as temporary:
            missing_root = Path(temporary) / "cache" / "market" / "plugin" / "1.0.2"
            missing_root.parent.mkdir(parents=True)
            env = os.environ.copy()
            env["PLUGIN_ROOT"] = str(missing_root)
            result = subprocess.run(
                [sys.executable, "-c", python_source],
                input=json.dumps({"prompt": "Run PowerShell on Windows."}),
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", output["additionalContext"])

    def test_inline_boundary_replaces_blocking_invalid_and_nonzero_dispatchers(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        scenarios = {
            "nested-decision": (
                "import json\nprint(json.dumps({'hookSpecificOutput': {"
                "'hookEventName': 'UserPromptSubmit', 'additionalContext': 'bad', "
                "'nested': {'decision': 'block'}}}))\n"
            ),
            "nested-continue": (
                "import json\nprint(json.dumps({'hookSpecificOutput': {"
                "'hookEventName': 'UserPromptSubmit', 'additionalContext': 'bad', "
                "'nested': {'continue': False}}}))\n"
            ),
            "invalid-json": "print('not-json')\n",
            "nonzero": "raise SystemExit(9)\n",
        }

        for name, dispatcher_source in scenarios.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                versions = Path(temporary) / "cache" / "market" / "plugin"
                original = versions / "1.0.3"
                newest = versions / "1.0.4+codex.local-malicious"
                shutil.copytree(PLUGIN_ROOT, original)
                shutil.copytree(PLUGIN_ROOT, newest)
                _set_manifest_version(original, "1.0.3")
                _set_manifest_version(newest, "1.0.4")
                (newest / "hooks" / "version_resilient_dispatch.py").write_text(
                    dispatcher_source,
                    encoding="utf-8",
                )

                result = _run_loaded_posix_command(
                    command,
                    {"prompt": "Run PowerShell on Windows."},
                    plugin_root=original,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                output = _assert_nonblocking_hook_payload(self, result.stdout)
                context = output["additionalContext"]
                self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", context)
                self.assertIn("Continue the current user task", context)
                self.assertNotIn("decision", result.stdout.casefold())
                self.assertNotIn('"continue":false', result.stdout.casefold())

    def test_inline_boundary_times_out_a_hanging_newest_dispatcher(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as temporary:
            versions = Path(temporary) / "cache" / "market" / "plugin"
            original = versions / "1.0.3"
            newest = versions / "1.0.4+codex.local-hanging"
            shutil.copytree(PLUGIN_ROOT, original)
            shutil.copytree(PLUGIN_ROOT, newest)
            _set_manifest_version(original, "1.0.3")
            _set_manifest_version(newest, "1.0.4")
            (newest / "hooks" / "version_resilient_dispatch.py").write_text(
                "import time\ntime.sleep(30)\n",
                encoding="utf-8",
            )

            started = time.monotonic()
            result = _run_loaded_posix_command(
                command,
                {"prompt": "Run PowerShell on Windows."},
                plugin_root=original,
            )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 4.5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", output["additionalContext"])

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

    def test_unsafe_or_oversized_skill_content_degrades_without_blocking(self) -> None:
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
                    self.assertEqual(result.returncode, 0, result.stderr)
                    output = _assert_nonblocking_hook_payload(self, result.stdout)
                    context = output["additionalContext"]
                    self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", context)
                    self.assertIn("Continue the user's task", context)
                    self.assertIn(expected_error, context)
                    self.assertNotIn("ROUTE BLOCKED", context)
                    self.assertNotIn(content, context)

    def test_dispatcher_failure_is_nonblocking_and_actionable(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as temporary:
            plugin_root = Path(temporary) / "plugin" / "1.0.3"
            shutil.copytree(PLUGIN_ROOT, plugin_root)
            (plugin_root / "hooks" / "windows_prompt_route.py").write_text(
                "this is invalid python !!!\n",
                encoding="utf-8",
            )
            result = _run_loaded_posix_command(
                command,
                {"prompt": "Run PowerShell on Windows."},
                plugin_root=plugin_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output = _assert_nonblocking_hook_payload(self, result.stdout)
            self.assertIn("WINDOWS COMMAND ROUTING DEGRADED", output["additionalContext"])
            self.assertIn("Continue the user's task", output["additionalContext"])

    def test_dispatcher_failure_keeps_unrelated_copy_prompt_silent(self) -> None:
        config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
        command = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]

        with tempfile.TemporaryDirectory() as temporary:
            plugin_root = Path(temporary) / "plugin" / "1.0.3"
            shutil.copytree(PLUGIN_ROOT, plugin_root)
            (plugin_root / "hooks" / "windows_prompt_route.py").write_text(
                "this is invalid python !!!\n",
                encoding="utf-8",
            )
            result = _run_loaded_posix_command(
                command,
                {
                    "prompt": (
                        "Make the README title searchable for Windows, PowerShell, "
                        "WSL, and SSH."
                    )
                },
                plugin_root=plugin_root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertEqual(result.stderr, "")

    def test_active_hook_sources_cannot_emit_blocking_control_fields(self) -> None:
        active_sources = (
            HOOK_PATH.read_text(encoding="utf-8"),
            DISPATCH_PATH.read_text(encoding="utf-8"),
            HOOK_CONFIG_PATH.read_text(encoding="utf-8"),
        )
        for source in active_sources:
            with self.subTest(source=source[:60]):
                compact = "".join(source.casefold().split())
                self.assertNotIn('"decision":"block"', compact)
                self.assertNotIn("'decision':'block'", compact)
                self.assertNotIn('"continue":false', compact)
                self.assertNotIn("'continue':false", compact)
                self.assertNotIn("return2", compact)
                self.assertNotIn("exit(2)", compact)

    def test_package_validator_guards_embedded_bootstrap_and_nonblocking_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PACKAGE_VALIDATOR_PATH)],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Package validation passed", result.stdout)

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
