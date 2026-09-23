"""Exercise PowerShell entry points without touching project outputs or history."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


@unittest.skipUnless(POWERSHELL, "PowerShell is not installed")
class LaunchScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="launch scripts ")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.project = self.directory / "project with spaces"
        self.project.mkdir()
        for name in ("verify.ps1", "run.ps1", "run_app.ps1"):
            shutil.copyfile(ROOT / name, self.project / name)
        self.python = self.project / ".venv" / "Scripts" / "python.exe"
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        self.playwright = self.project / "node_modules" / "playwright" / "package.json"
        self.playwright.parent.mkdir(parents=True)
        self.playwright.write_text("{}", encoding="ascii")
        self.log = self.directory / "commands.jsonl"

    def run_script(self, name="verify.ps1", arguments=(), *, failure="", no_node=False, multiple_tools=False):
        # Aliases allow real PowerShell parsing, argument binding, location handling,
        # and exit propagation while keeping every native child command isolated.
        wrapper = self.directory / "invoke.ps1"
        wrapper.write_text(
            r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
function Invoke-TestCommand {
    param([string]$Tool, [object[]]$CommandArguments)
    @{tool=$Tool; args=@($CommandArguments); cwd=(Get-Location).Path} |
        ConvertTo-Json -Compress | Add-Content -LiteralPath $env:TEST_COMMAND_LOG -Encoding UTF8
    $global:LASTEXITCODE = 0
    if ($env:TEST_FAIL -and (($CommandArguments -join ' ').Contains($env:TEST_FAIL))) {
        $global:LASTEXITCODE = 23
    }
}
function Stub-Python { Invoke-TestCommand 'python' @($args) }
function Stub-Node { Invoke-TestCommand 'node' @($args) }
function Stub-Npm { Invoke-TestCommand 'npm' @($args) }
Set-Alias -Name (Join-Path $env:TEST_PROJECT '.venv\Scripts\python.exe') -Value Stub-Python
Set-Alias -Name (Join-Path $env:TEST_PROJECT 'node.exe') -Value Stub-Node
Set-Alias -Name (Join-Path $env:TEST_PROJECT 'npm.cmd') -Value Stub-Npm
function Get-Command {
    [CmdletBinding()]
    param([string]$Name, [object]$CommandType)
    if ($env:TEST_NO_NODE -eq '1') { return $null }
    if ($Name -eq 'node' -or $Name -eq 'npm.cmd') {
        $executableName = if ($Name -eq 'node') { 'node.exe' } else { 'npm.cmd' }
        [pscustomobject]@{Source=(Join-Path $env:TEST_PROJECT $executableName)}
        if ($env:TEST_MULTIPLE_TOOLS -eq '1') {
            [pscustomobject]@{Source=(Join-Path $env:TEST_PROJECT ('other installation\' + $executableName))}
        }
        return
    }
    throw "Unexpected command lookup: $Name"
}
& (Join-Path $env:TEST_PROJECT $env:TEST_SCRIPT) @args
$scriptExitCode = $LASTEXITCODE
Write-Output ('RESTORED:' + (Get-Location).Path)
exit $scriptExitCode
""",
            encoding="ascii",
        )
        env = dict(os.environ, TEST_PROJECT=str(self.project), TEST_SCRIPT=name,
                   TEST_COMMAND_LOG=str(self.log), TEST_FAIL=failure,
                   TEST_NO_NODE="1" if no_node else "0",
                   TEST_MULTIPLE_TOOLS="1" if multiple_tools else "0")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), *arguments],
            cwd=self.directory, env=env, capture_output=True, timeout=30,
        )
        records = ([json.loads(line) for line in self.log.read_text(encoding="utf-8-sig").splitlines()]
                   if self.log.exists() else [])
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        return result.returncode, records, output

    def test_python_only_mode_rebuilds_and_requires_no_node(self):
        code, records, output = self.run_script(arguments=("-SkipUi",), no_node=True)
        self.assertEqual(code, 0, output)
        self.assertEqual([row["tool"] for row in records], ["python"] * 3)
        self.assertEqual([" ".join(row["args"]) for row in records], [
            "-X utf8 -m pip check", "-X utf8 run_pipeline.py",
            "-X utf8 -m unittest discover -s tests -q",
        ])
        self.assertTrue(all(Path(row["cwd"]) == self.project for row in records))
        self.assertIn("RESTORED:" + str(self.directory), output)

    def test_full_mode_checks_current_artifact_before_browser_suites(self):
        code, records, output = self.run_script()
        self.assertEqual(code, 0, output)
        self.assertEqual([row["tool"] for row in records], ["python"] * 3 + ["node"] * 2 + ["npm"] * 2)
        self.assertEqual([row["args"] for row in records[-4:]], [
            ["scripts/check_graph_view.js"], ["scripts/check_theme.js"],
            ["run", "test:ui"], ["run", "test:workbench"],
        ])

    def test_full_mode_uses_first_node_and_npm_installation_on_path(self):
        code, records, output = self.run_script(multiple_tools=True)
        self.assertEqual(code, 0, output)
        self.assertEqual([row["tool"] for row in records], ["python"] * 3 + ["node"] * 2 + ["npm"] * 2)

    def test_failed_command_stops_following_steps_and_preserves_exit_code(self):
        for expected_count, failure in enumerate((
            "pip check", "run_pipeline.py", "unittest discover", "check_graph_view.js",
            "check_theme.js", "test:ui", "test:workbench",
        ), start=1):
            with self.subTest(command=failure):
                self.log.unlink(missing_ok=True)
                code, records, output = self.run_script(failure=failure)
                self.assertEqual(code, 23, output)
                self.assertEqual(len(records), expected_count, records)
                self.assertNotIn("Project verification passed.", output)

    def test_full_mode_reports_missing_node_before_running_any_steps(self):
        code, records, output = self.run_script(no_node=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(records, [])
        self.assertIn("Full verification requires Node.js and npm", output)

    def test_full_mode_reports_missing_playwright_before_running_any_steps(self):
        self.playwright.unlink()
        code, records, output = self.run_script()
        self.assertNotEqual(code, 0)
        self.assertEqual(records, [])
        self.assertIn("Missing UI test dependencies", output)

    def test_missing_python_is_actionable(self):
        self.python.unlink()
        for script in ("verify.ps1", "run.ps1", "run_app.ps1"):
            with self.subTest(script=script):
                code, records, output = self.run_script(script)
                self.assertNotEqual(code, 0)
                self.assertEqual(records, [])
                self.assertIn("Missing .venv", output)

    def test_launchers_preserve_native_failure_and_space_containing_arguments(self):
        for script, arguments, failure in (
            ("run.ps1", (), "run_pipeline.py"),
            ("run_app.ps1", ("-Port", "9999", "-DataDir", "storage with spaces"), "run_app.py"),
        ):
            with self.subTest(script=script):
                self.log.unlink(missing_ok=True)
                code, records, output = self.run_script(script, arguments, failure=failure)
                self.assertEqual(code, 23, output)
                self.assertEqual(len(records), 1)
                self.assertTrue(records[0]["args"][2].endswith(failure))
                if script == "run_app.ps1":
                    self.assertEqual(records[0]["args"][3:], ["--port", "9999", "--data-dir", "storage with spaces"])


if __name__ == "__main__":
    unittest.main()
