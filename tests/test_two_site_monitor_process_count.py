"""Use real pgrep against owned sleep processes with synthetic command lines."""
from pathlib import Path
import subprocess

import pytest


MONITOR = Path(__file__).resolve().parents[1] / "scripts/two_site_autopilot_monitor.sh"


@pytest.mark.parametrize("service", ["supervisor", "broker"])
@pytest.mark.parametrize("daemon_count", [0, 1, 2])
def test_daemons_count_but_retained_tmux_launch_commands_do_not(tmp_path, service, daemon_count):
    source = MONITOR.read_text()
    marker = service + '_count="$(/usr/bin/pgrep -fc "'
    pattern = source.split(marker, 1)[1].split('"', 1)[0].replace("$integration_root", str(tmp_path))
    script = "two_site_autopilot.py" if service == "supervisor" else "two_site_provider_broker.py"
    command = f"/usr/bin/python3 {tmp_path}/scripts/{script} --root {tmp_path}"
    command += " --quota-wait-enabled" if service == "supervisor" else " serve --quota-continuation-enabled"
    commands = ["tmux new-session -d -s fixture " + command] + [command] * daemon_count
    processes = []
    try:
        for argv0 in commands:
            processes.append(subprocess.Popen([argv0, "30"], executable="/usr/bin/sleep"))
        result = subprocess.run(["/usr/bin/pgrep", "-fc", pattern], capture_output=True,
                                text=True, timeout=5)
        assert result.returncode == (0 if daemon_count else 1)
        assert int(result.stdout.strip()) == daemon_count
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=5)
