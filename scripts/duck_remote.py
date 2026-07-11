import argparse
import getpass
import os
import posixpath
import stat
import sys
import time
from pathlib import Path

DEFAULT_HOST = "192.168.0.34"
DEFAULT_USER = "duck"
DEFAULT_REMOTE_DIR = "/home/duck/open_duck_mini_runtime"
DEFAULT_LOG_FILE = "/tmp/duck.log"
EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".swp", ".tmp"}


def should_skip(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & EXCLUDED_DIRS) or path.suffix in EXCLUDED_SUFFIXES


class DuckRemote:
    def __init__(self, host, user, password, remote_dir):
        try:
            import paramiko
        except ImportError as exc:
            print(
                "Missing dependency: paramiko. Install it with: python -m pip install paramiko",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

        self.host = host
        self.user = user
        self.password = password
        self.remote_dir = remote_dir.rstrip("/")
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def __enter__(self):
        self.client.connect(
            hostname=self.host,
            username=self.user,
            password=self.password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        self.client.close()

    def run(self, command, check=True, stream=False):
        stdin, stdout, stderr = self.client.exec_command(command)
        if stream:
            while not stdout.channel.exit_status_ready():
                if stdout.channel.recv_ready():
                    print(stdout.channel.recv(4096).decode("utf-8", "replace"), end="")
                if stderr.channel.recv_stderr_ready():
                    print(stderr.channel.recv_stderr(4096).decode("utf-8", "replace"), end="", file=sys.stderr)
                time.sleep(0.1)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        if out:
            print(out, end="")
        if err:
            print(err, end="", file=sys.stderr)
        code = stdout.channel.recv_exit_status()
        if check and code:
            raise SystemExit(code)
        return code

    def sync(self, local_dir: Path):
        sftp = self.client.open_sftp()
        try:
            self._mkdir_p(sftp, self.remote_dir)
            uploaded = 0
            skipped = 0
            for path in local_dir.rglob("*"):
                rel = path.relative_to(local_dir)
                if should_skip(rel):
                    skipped += 1
                    continue
                remote_path = posixpath.join(self.remote_dir, rel.as_posix())
                if path.is_dir():
                    self._mkdir_p(sftp, remote_path)
                    continue
                self._mkdir_p(sftp, posixpath.dirname(remote_path))
                if self._same_file(sftp, path, remote_path):
                    skipped += 1
                    continue
                sftp.put(str(path), remote_path)
                uploaded += 1
            print(f"Synced to {self.user}@{self.host}:{self.remote_dir} ({uploaded} uploaded, {skipped} skipped)")
        finally:
            sftp.close()

    def stop_duck(self):
        self.run(
            "python3 - <<'PY'\n"
            "import os, signal, subprocess\n"
            "target = 'v2_rl_walk_' + 'mujoco.py'\n"
            "current = os.getpid()\n"
            "for line in subprocess.check_output(['ps', '-eo', 'pid=,args='], text=True).splitlines():\n"
            "    pid_text, _, args = line.strip().partition(' ')\n"
            "    if not pid_text.isdigit():\n"
            "        continue\n"
            "    pid = int(pid_text)\n"
            "    if pid != current and target in args:\n"
            "        print(f'killing {pid} {args}')\n"
            "        os.kill(pid, signal.SIGTERM)\n"
            "PY"
        )
        self.run(
            "python3 - <<'PY'\n"
            "import subprocess\n"
            "ids = [20, 21, 22, 23, 24, 30, 31, 32, 33, 10, 11, 12, 13, 14]\n"
            "code = \"import rustypot; ids=%r; io=rustypot.Sts3215PyController('/dev/ttyACM0', 1000000, 0.08); io.sync_write_torque_enable(ids, [False] * len(ids)); print('torque disabled')\" % ids\n"
            "subprocess.run(['/home/duck/.venv/bin/python', '-c', code], check=False)\n"
            "PY",
            check=False,
        )

    def _mkdir_p(self, sftp, remote_path):
        parts = remote_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                sftp.mkdir(current)
            except OSError:
                pass

    def _same_file(self, sftp, local_path: Path, remote_path: str) -> bool:
        try:
            remote_stat = sftp.stat(remote_path)
        except OSError:
            return False
        local_stat = local_path.stat()
        return (
            stat.S_ISREG(remote_stat.st_mode)
            and remote_stat.st_size == local_stat.st_size
            and local_stat.st_mtime <= remote_stat.st_mtime + 1
        )


def runtime_prefix(remote_dir):
    candidates = (
        "$HOME/.venv/bin/python",
        "$HOME/.venvs/open_duck_mini_runtime/bin/python",
        "$HOME/.virtualenvs/open-duck-mini-runtime/bin/python",
    )
    detect_python = " ; ".join(f"[ $PYTHON_OK -eq 0 ] && [ -x {path} ] && PYTHON={path} && PYTHON_OK=1" for path in candidates)
    return (
        f"cd {remote_dir} && "
        f"PYTHON=python3; PYTHON_OK=0; {detect_python}; "
        "export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy PYTHONUNBUFFERED=1; "
    )


def build_parser():
    parser = argparse.ArgumentParser(description="Deploy and control Open Duck Mini on the remote duck host.")
    parser.add_argument("command", choices=("sync", "check", "start", "stop", "restart", "status", "log"))
    parser.add_argument("--host", default=os.environ.get("DUCK_HOST", DEFAULT_HOST))
    parser.add_argument("--user", default=os.environ.get("DUCK_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("DUCK_PASSWORD"))
    parser.add_argument("--remote-dir", default=os.environ.get("DUCK_REMOTE_DIR", DEFAULT_REMOTE_DIR))
    parser.add_argument("--log-file", default=os.environ.get("DUCK_LOG_FILE", DEFAULT_LOG_FILE))
    parser.add_argument("--model", default=os.environ.get("DUCK_ONNX_MODEL", "BEST_WALK_ONNX_2.onnx"))
    parser.add_argument("--config", default=os.environ.get("DUCK_CONFIG", "duck_config.json"))
    parser.add_argument("--control-freq", type=int, default=50)
    parser.add_argument("--kp", type=int, default=22)
    parser.add_argument("--kd", type=int, default=0)
    parser.add_argument("--action-scale", type=float, default=0.2)
    parser.add_argument("--min-motor-voltage", type=float, default=6.8)
    parser.add_argument("--commands", dest="commands", action="store_true", default=True)
    parser.add_argument("--no-commands", dest="commands", action="store_false")
    return parser


def main():
    args = build_parser().parse_args()
    password = args.password or getpass.getpass(f"Password for {args.user}@{args.host}: ")
    local_dir = Path(__file__).resolve().parents[1]
    prefix = runtime_prefix(args.remote_dir)

    with DuckRemote(args.host, args.user, password, args.remote_dir) as duck:
        if args.command in ("sync", "start", "restart"):
            duck.sync(local_dir)

        if args.command == "sync":
            return
        if args.command == "check":
            duck.run(
                "ls -la /dev/ttyACM0; "
                "ls /dev/input/js0 2>/dev/null || true; "
                "test -e /dev/i2c-1 && echo 'I2C device present' || echo 'I2C device missing'"
            )
            return
        if args.command in ("stop", "restart"):
            duck.stop_duck()
            if args.command == "stop":
                return
            time.sleep(2)
        if args.command == "start" or args.command == "restart":
            if args.command == "start":
                duck.stop_duck()
            commands_flag = "--commands" if args.commands else "--no-commands"
            command = (
                f"{prefix}"
                f"nohup $PYTHON -u scripts/v2_rl_walk_mujoco.py "
                f"--onnx_model_path {args.model} "
                f"--duck_config_path {posixpath.join(args.remote_dir, args.config)} "
                f"{commands_flag} -c {args.control_freq} -p {args.kp} -d {args.kd} "
                f"--action_scale {args.action_scale} "
                f"--min_motor_voltage {args.min_motor_voltage} "
                f"> {args.log_file} 2>&1 &"
            )
            duck.run(command)
            duck.run("pgrep -af '[v]2_rl_walk_mujoco' || true")
            return
        if args.command == "status":
            duck.run(f"pgrep -af '[v]2_rl_walk_mujoco' || true; tail -20 {args.log_file} 2>/dev/null || true")
            return
        if args.command == "log":
            duck.run(f"tail -f {args.log_file}", check=False, stream=True)


if __name__ == "__main__":
    main()
