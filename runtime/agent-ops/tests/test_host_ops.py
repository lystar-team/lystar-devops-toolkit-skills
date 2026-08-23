from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = Path(os.environ.get("AGENT_OPS_BIN_DIR", REPO_ROOT / "bin"))
sys.path.insert(0, str(SCRIPTS))

import host_ops


FAKE_SSHX = r"""#!/usr/bin/env python3
import json
import os
import sys

command = sys.argv[-1]
marker = command.splitlines()[0] if command.splitlines() else ""
mode = os.environ.get("HOSTX_FAKE_MODE", "")

if mode == "unavailable":
    print(json.dumps({"error": "SSH daemon unavailable"}))
    raise SystemExit(1)

if mode == "missing" and marker == "# hostx:ports":
    print(json.dumps({
        "stdout": "ss command not found\n",
        "stderr": "ss: not found\n",
        "exit_code": 127,
    }))
    raise SystemExit(1)

outputs = {
    "# hostx:facts": (
        "hostname\ttest-host\n"
        "os\tLinux\n"
        "kernel\t6.8.0-test\n"
        "arch\tx86_64\n"
        "uptime_seconds\t123.5\n"
        "load_1\t0.10\n"
        "load_5\t0.20\n"
        "load_15\t0.30\n"
    ),
    "# hostx:service": (
        "name\tnginx\n"
        "manager\tsystemd\n"
        "load_state\tloaded\n"
        "active\tactive\n"
        "substate\trunning\n"
        "pid\t321\n"
        "enabled\tenabled\n"
    ),
    "# hostx:service_inspect": (
        "__HOSTX_INSPECT_META__\tId\tnginx.service\n"
        "__HOSTX_INSPECT_META__\tLoadState\tloaded\n"
        "__HOSTX_INSPECT_META__\tActiveState\tactive\n"
        "__HOSTX_INSPECT_META__\tSubState\trunning\n"
        "__HOSTX_INSPECT_META__\tMainPID\t321\n"
        "__HOSTX_INSPECT_META__\tUnitFileState\tenabled\n"
        "__HOSTX_INSPECT_META__\tFragmentPath\t/etc/systemd/system/nginx.service\n"
        "__HOSTX_INSPECT_META__\tDropInPaths\t/etc/systemd/system/nginx.service.d/10-env.conf\n"
        "__HOSTX_INSPECT_META__\tExecStart\t{ path=/opt/web/current/bin/web ; argv[]=/opt/web/current/bin/web --config /etc/web/config.yaml --data-dir /srv/web/data --log-file /var/log/web/app.log ; }\n"
        "__HOSTX_INSPECT_META__\tUser\tweb\n"
        "__HOSTX_INSPECT_META__\tWorkingDirectory\t/opt/web/current\n"
        "__HOSTX_INSPECT_META__\tEnvironmentFiles\t-/etc/web/web.env\n"
        "__HOSTX_INSPECT_META__\tAfter\tnetwork.target\n"
        "__HOSTX_INSPECT_META__\tRequires\tnetwork.target\n"
        "__HOSTX_INSPECT_META__\tConfigurationDirectory\tweb\n"
        "__HOSTX_INSPECT_META__\tStateDirectory\tweb\n"
        "__HOSTX_INSPECT_META__\tLogsDirectory\tweb\n"
        "__HOSTX_INSPECT_META__\tControlGroup\t/system.slice/nginx.service\n"
        "__HOSTX_INSPECT_META__\tCGroupPids\t321 322\n"
        "__HOSTX_INSPECT_UNIT_BEGIN__\n"
        "[Unit]\n"
        "After=network.target\n"
        "Requires=network.target\n"
        "[Service]\n"
        "ExecStart=/opt/web/current/bin/web --config /etc/web/config.yaml --data-dir /srv/web/data --log-file /var/log/web/app.log\n"
        "User=web\n"
        "WorkingDirectory=/opt/web/current\n"
        "EnvironmentFile=-/etc/web/web.env\n"
        "__HOSTX_INSPECT_UNIT_END__\n"
        "__HOSTX_INSPECT_DROPIN_BEGIN__\t/etc/systemd/system/nginx.service.d/10-env.conf\n"
        "[Service]\n"
        "EnvironmentFile=-/etc/web/override.env\n"
        "__HOSTX_INSPECT_DROPIN_END__\n"
        "__HOSTX_INSPECT_PROCESS_BEGIN__\n"
        "321 1 web S 0.1 0.2 /opt/web/current/bin/web --config /etc/web/config.yaml --data-dir /srv/web/data --log-file /var/log/web/app.log\n"
        "__HOSTX_INSPECT_PROCESS_END__\n"
        "__HOSTX_INSPECT_META__\tProcessCwd\t/opt/web/current\n"
        "__HOSTX_INSPECT_META__\tProcessExe\t/opt/web/current/bin/web\n"
        "__HOSTX_INSPECT_META__\tProcessCommand\t/opt/web/current/bin/web --config /etc/web/config.yaml --data-dir /srv/web/data --log-file /var/log/web/app.log\n"
        "__HOSTX_INSPECT_PORTS_BEGIN__\n"
        "tcp LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:((\"web\",pid=321,fd=7))\n"
        "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\",pid=999,fd=3))\n"
        "__HOSTX_INSPECT_PORTS_END__\n"
    ),
    "# hostx:process_list": (
        "100 1 root S 0.1 0.2 python worker\n"
        "101 1 app S 1.0 2.0 nginx: master process\n"
    ),
    "# hostx:process_show": "101 1 app S 1.0 2.0 nginx: master process\n",
    "# hostx:logs": "2026-08-22T10:00:00+0800 line-1\nline-2\n",
    "# hostx:ports": (
        'tcp LISTEN 0 128 127.0.0.1:22 0.0.0.0:* users:(("sshd",pid=222,fd=3))\n'
        'udp UNCONN 0 0 [::]:5353 [::]:* users:(("dns",pid=333,fd=4))\n'
    ),
        "# hostx:disk": (
        "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/root 100000 25000 75000 25% /\n"
    ),
}

stdout = outputs.get(marker, "")
if mode == "inspect_missing_ports" and marker == "# hostx:service_inspect":
    stdout = stdout.replace(
        "__HOSTX_INSPECT_PORTS_BEGIN__\n"
        "tcp LISTEN 0 128 127.0.0.1:8080 0.0.0.0:* users:((\"web\",pid=321,fd=7))\n"
        "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\",pid=999,fd=3))\n"
        "__HOSTX_INSPECT_PORTS_END__\n",
        "__HOSTX_INSPECT_META__\tPortsError\tss command not found\n"
        "__HOSTX_INSPECT_PORTS_BEGIN__\n"
        "__HOSTX_INSPECT_PORTS_END__\n",
    )
if mode == "inspect_not_found" and marker == "# hostx:service_inspect":
    stdout = (
        "__HOSTX_INSPECT_META__\tId\tmissing.service\n"
        "__HOSTX_INSPECT_META__\tLoadState\tnot-found\n"
        "__HOSTX_INSPECT_META__\tActiveState\tinactive\n"
    )
    print(json.dumps({"stdout": stdout, "stderr": "", "exit_code": 3}))
    raise SystemExit(1)
if mode == "truncated" and marker == "# hostx:logs":
    stdout = "line-1\n... omitted 12 bytes ...\nline-tail\n"
print(json.dumps({"stdout": stdout, "stderr": "", "exit_code": 0}))
"""


class HostOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake_sshx = self.root / "fake-sshx"
        self.fake_sshx.write_text(FAKE_SSHX, encoding="utf-8")
        self.fake_sshx.chmod(0o755)
        self.env = {
            **os.environ,
            "XDG_DATA_HOME": str(REPO_ROOT / "runtime"),
            "LYSTAR_SKILL_AUTO_UPDATE": "0",
            "HOSTX_SSHX": str(self.fake_sshx),
        }
        self.hostx = str(BIN_DIR / "hostx")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_hostx(self, *args: str, mode: str = "") -> subprocess.CompletedProcess[str]:
        env = {**self.env, "HOSTX_FAKE_MODE": mode}
        return subprocess.run(
            [self.hostx, *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def test_collectors_return_stable_json_and_human_output(self) -> None:
        facts = self.run_hostx("facts", "prod", "--json")
        self.assertEqual(facts.returncode, 0, facts.stderr)
        facts_payload = json.loads(facts.stdout)
        self.assertEqual(facts_payload["status"], "ok")
        self.assertEqual(facts_payload["target"]["alias"], "prod")
        self.assertEqual(facts_payload["facts"]["hostname"], "test-host")
        self.assertEqual(facts_payload["facts"]["load"]["5m"], 0.2)

        service = self.run_hostx("service", "prod", "status", "nginx", "--json")
        self.assertEqual(json.loads(service.stdout)["service"]["active"], "active")

        inspect = self.run_hostx("service", "prod", "inspect", "nginx.service", "--json")
        self.assertEqual(inspect.returncode, 0, inspect.stderr)
        inspect_payload = json.loads(inspect.stdout)
        self.assertEqual(inspect_payload["kind"], "service_inspect")
        self.assertEqual(inspect_payload["status"], "ok")
        self.assertEqual(inspect_payload["service"]["load_state"], "loaded")
        self.assertEqual(inspect_payload["user"], "web")
        self.assertEqual(inspect_payload["working_directory"], "/opt/web/current")
        self.assertEqual(inspect_payload["environment_files"][0]["path"], "/etc/web/web.env")
        self.assertEqual(inspect_payload["dependencies"]["requires"], ["network.target"])
        self.assertEqual(inspect_payload["unit_file"]["path"], "/etc/systemd/system/nginx.service")
        self.assertEqual(len(inspect_payload["drop_ins"]), 1)
        self.assertEqual(inspect_payload["process"]["pid"], 321)
        self.assertEqual(inspect_payload["ports"][0]["port"], 8080)
        self.assertEqual(inspect_payload["ports_scope"], "service")
        self.assertIn("/opt/web/current", inspect_payload["paths"]["release"])
        self.assertIn("/etc/web/config.yaml", inspect_payload["paths"]["config"])
        self.assertIn("/srv/web/data", inspect_payload["paths"]["data"])
        self.assertIn("/var/log/web/app.log", inspect_payload["paths"]["log"])

        processes = self.run_hostx("process", "prod", "list", "--pattern", "nginx", "--json")
        process_payload = json.loads(processes.stdout)
        self.assertEqual(len(process_payload["processes"]), 1)
        self.assertEqual(process_payload["processes"][0]["pid"], 101)

        process = self.run_hostx("process", "prod", "show", "101", "--json")
        self.assertEqual(json.loads(process.stdout)["processes"][0]["command"], "nginx: master process")

        logs = self.run_hostx("logs", "prod", "/var/log/example.log", "--lines", "2", "--json")
        self.assertEqual(json.loads(logs.stdout)["log"]["lines"], ["2026-08-22T10:00:00+0800 line-1", "line-2"])

        ports = self.run_hostx("ports", "prod", "--json")
        ports_payload = json.loads(ports.stdout)
        self.assertEqual(ports_payload["ports"][0]["port"], 22)
        self.assertEqual(ports_payload["ports"][0]["pid"], 222)
        self.assertEqual(ports_payload["ports"][1]["local_address"], "::")

        disk = self.run_hostx("disk", "prod", "/", "--json")
        self.assertEqual(json.loads(disk.stdout)["disks"][0]["total_bytes"], 102400000)

        health = self.run_hostx(
            "health",
            "prod",
            "--check",
            "facts,disk:/,ports",
            "--json",
        )
        health_payload = json.loads(health.stdout)
        self.assertEqual(health_payload["status"], "pass")
        self.assertEqual([item["status"] for item in health_payload["checks"]], ["pass", "pass", "pass"])

        human = self.run_hostx("facts", "prod")
        self.assertEqual(human.returncode, 0)
        self.assertIn("# kind=facts target=prod status=ok", human.stdout)
        self.assertIn("hostname=test-host", human.stdout)

    def test_missing_command_and_truncated_output_are_explicit(self) -> None:
        missing = self.run_hostx("ports", "prod", "--json", mode="missing")
        self.assertNotEqual(missing.returncode, 0)
        missing_payload = json.loads(missing.stdout)
        self.assertEqual(missing_payload["status"], "unknown")
        self.assertEqual(missing_payload["availability"], "missing")

        truncated = self.run_hostx("logs", "prod", "/var/log/example.log", "--json", mode="truncated")
        self.assertEqual(truncated.returncode, 0)
        truncated_payload = json.loads(truncated.stdout)
        self.assertEqual(truncated_payload["status"], "partial")
        self.assertTrue(truncated_payload["log"]["truncated"])

        inspect = self.run_hostx("service", "prod", "inspect", "nginx.service", "--json", mode="inspect_missing_ports")
        self.assertEqual(inspect.returncode, 0)
        inspect_payload = json.loads(inspect.stdout)
        self.assertEqual(inspect_payload["status"], "partial")
        self.assertEqual(inspect_payload["ports_status"], "unknown")
        self.assertEqual(inspect_payload["ports"], [])

        missing_service = self.run_hostx(
            "service", "prod", "inspect", "missing.service", "--json", mode="inspect_not_found"
        )
        self.assertNotEqual(missing_service.returncode, 0)
        missing_service_payload = json.loads(missing_service.stdout)
        self.assertEqual(missing_service_payload["status"], "failed")
        self.assertEqual(missing_service_payload["service"]["error"], "服务单元不存在")

    def test_ssh_unavailable_is_not_host_failure(self) -> None:
        facts = self.run_hostx("facts", "prod", "--json", mode="unavailable")
        self.assertNotEqual(facts.returncode, 0)
        facts_payload = json.loads(facts.stdout)
        self.assertEqual(facts_payload["status"], "unavailable")
        self.assertEqual(facts_payload["connection_status"], "unavailable")

        health = self.run_hostx("health", "prod", "--json", mode="unavailable")
        health_payload = json.loads(health.stdout)
        self.assertEqual(health_payload["status"], "unknown")
        self.assertEqual(health_payload["connection_status"], "unavailable")
        self.assertTrue(all(item["status"] == "unknown" for item in health_payload["checks"]))


class HostOpsParserTest(unittest.TestCase):
    def test_parser_handles_ipv6_and_disk_fields(self) -> None:
        ports, warnings = host_ops.parse_ss_lines(
            'tcp LISTEN 0 128 [::1]:443 [::]:* users:(("web",pid=9,fd=5))\n'
        )
        self.assertFalse(warnings)
        self.assertEqual(ports[0]["local_address"], "::1")
        self.assertEqual(ports[0]["port"], 443)
        self.assertEqual(host_ops.parse_df_output(
            "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
            "/dev/vda1 10 2 8 20% /\n",
            "/",
        )[0]["used_percent"], 20.0)
        with self.assertRaises(ValueError):
            host_ops.logs_command("/var/log/example.log", 10, "1 hour ago")


if __name__ == "__main__":
    unittest.main()
