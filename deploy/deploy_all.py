"""One-command deployment of the Pond Planning System to the four API nodes.

Security: the SSH password is read from the DEPLOY_SSH_PW environment variable
at runtime; it is never written to the bundle, the servers' repository copies,
or this file.

Usage (from the repository root):
  python deploy/deploy_all.py bundle                 # build deploy bundle
  python deploy/deploy_all.py sync                   # FAST: sync app code directly & reload in 2s (no pip)
  python deploy/deploy_all.py push                   # full push: upload+setup on all 4 nodes
  python deploy/deploy_all.py nginx                  # install/reload pond nginx site (sys1)
  python deploy/deploy_all.py verify                 # check /version on all nodes + LB port
  python deploy/deploy_all.py stop-node <ssh_port>   # stop one node's daemon (failover test)
  python deploy/deploy_all.py start-node <ssh_port>  # start one node's daemon
"""
import argparse
import io
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deploy.ssh_node import connect_with_retry  # noqa: E402

NODES = [
    {"ssh_port": 2309, "node_id": "sys1", "internal_ip": "172.17.0.51"},
    {"ssh_port": 2310, "node_id": "sys2", "internal_ip": "172.17.0.5"},
    {"ssh_port": 2311, "node_id": "sys3", "internal_ip": "172.17.0.95"},
    {"ssh_port": 2312, "node_id": "sys4", "internal_ip": "172.17.0.50"},
]
PROXY_NODE = NODES[0]
REDIS_HOST = PROXY_NODE["internal_ip"]  # shared Redis discovered on sys1

BUNDLE_NAME = "pond_bundle.tgz"
BUNDLE_INCLUDE = ["app", "tests", "requirements.txt", ".env.example", "README.md",
                  "Dockerfile", "render.yaml", ".gitignore"]
BUNDLE_EXCLUDE_PARTS = {"venv", "__pycache__", ".git", ".pytest_cache", "data",
                        ".commandcode", "deploy", "node_modules"}


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return f"dev-{time.strftime('%Y%m%d%H%M%S')}"


def build_bundle() -> Path:
    out = REPO_ROOT / "deploy" / BUNDLE_NAME
    with tarfile.open(out, "w:gz") as tar:
        for item in BUNDLE_INCLUDE:
            path = REPO_ROOT / item
            if not path.exists():
                continue
            if path.is_file():
                tar.add(path, arcname=item)
            else:
                for f in path.rglob("*"):
                    if f.is_file() and not (BUNDLE_EXCLUDE_PARTS & set(f.parts[len(REPO_ROOT.parts):])):
                        if "__pycache__" in f.parts:
                            continue
                        tar.add(f, arcname=str(f.relative_to(REPO_ROOT)))
    print(f"bundle: {out} ({out.stat().st_size / 1024:.0f} KB)")
    return out


def node_env(node_id: str, commit: str) -> str:
    # Environment variables only — no credentials exist for Redis (internal trust domain).
    return (
        f"NODE_ID={node_id}\n"
        f"REDIS_URL=redis://{REDIS_HOST}:6379/1\n"
        f"APP_ENV=production\n"
        f"GIT_COMMIT={commit}\n"
        f"LOG_LEVEL=INFO\n"
    )


def deploy_node(password: str, node: dict, bundle: Path, commit: str) -> None:
    client = connect_with_retry(password, node["ssh_port"])
    sftp = client.open_sftp()
    remote_home = sftp.normalize(".")
    remote_dir = f"{remote_home}/pond_catchment_backend"

    # Fresh upload target: remove previous bundle, keep nothing stale.
    client.exec_command(f"rm -f /tmp/{BUNDLE_NAME}")
    print(f"[{node['node_id']}] uploading bundle...")
    sftp.put(str(bundle), f"/tmp/{BUNDLE_NAME}")

    print(f"[{node['node_id']}] extracting + installing deps + (re)starting daemon...")
    setup_local = Path(__file__).parent / "remote_setup.sh"
    sftp.put(str(setup_local), "/tmp/pond_setup.sh")

    env_file_local = Path(__file__).parent / f".env.{node['node_id']}"
    env_file_local.write_text(node_env(node["node_id"], commit), encoding="utf-8")
    sftp.put(str(env_file_local), "/tmp/pond.env")

    # Order matters: extract -> write .env -> start daemon (so the daemon sees
    # NODE_ID/REDIS_URL on first boot).
    cmd = (
        f"mkdir -p {remote_dir} && "
        f"tar -xzf /tmp/{BUNDLE_NAME} -C {remote_dir} && "
        f"mv /tmp/pond.env {remote_dir}/.env && "
        f"bash /tmp/pond_setup.sh"
    )
    stdin, stdout, stderr = client.exec_command(cmd, timeout=900)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()

    sftp.close()
    env_file_local.unlink(missing_ok=True)
    print(out)
    if err.strip():
        print(f"[{node['node_id']}] STDERR:\n{err}")
    if code != 0:
        raise SystemExit(f"[{node['node_id']}] setup failed with exit {code}")
    client.close()


def install_nginx(password: str) -> None:
    conf_local = Path(__file__).parent / "nginx" / "pond_lb.conf"
    client = connect_with_retry(password, PROXY_NODE["ssh_port"])
    sftp = client.open_sftp()
    sftp.put(str(conf_local), "/tmp/pond_lb.conf")
    sftp.close()
    inner = (
        "cp /tmp/pond_lb.conf /etc/nginx/sites-enabled/pond.conf && "
        "nginx -t && "
        "service nginx reload"
    )
    stdin, stdout, stderr = client.exec_command(
        f"sudo -S -p '' bash -c {repr(inner)}", timeout=60
    )
    stdin.write(password + "\n")
    stdin.flush()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    print(out)
    if err.strip():
        print("STDERR:", err)
    if code != 0:
        raise SystemExit(f"nginx install failed (exit {code})")
    client.close()


def verify(password: str) -> None:
    import urllib.request

    for node in NODES:
        client = connect_with_retry(password, node["ssh_port"])
        _in, out, _err = client.exec_command(
            "curl -s --max-time 5 http://127.0.0.1:8000/api/v1/version", timeout=30
        )
        print(f"[{node['node_id']}] {out.read().decode().strip()}")
        client.close()
    try:
        with urllib.request.urlopen(f"http://10.1.75.53:3309/api/v1/version", timeout=8) as resp:
            print(f"[lb:3309] {resp.read().decode().strip()}")
    except Exception as exc:
        print(f"[lb:3309] UNREACHABLE: {exc}")


def stop_start_node(password: str, ssh_port: int, action: str) -> None:
    client = connect_with_retry(password, ssh_port)
    if action == "stop-node":
        cmd = (
            "if [ -f ~/pond_catchment_backend/gunicorn.pid ]; then "
            "kill $(cat ~/pond_catchment_backend/gunicorn.pid); fi; sleep 1; "
            "curl -s --max-time 2 http://127.0.0.1:8000/api/v1/health || echo NODE-STOPPED"
        )
    else:
        cmd = (
            "cd ~/pond_catchment_backend && if [ -f gunicorn.pid ]; then "
            "kill $(cat gunicorn.pid) 2>/dev/null; fi; sleep 1; "
            "nohup ./venv/bin/gunicorn -k uvicorn.workers.UvicornWorker -w 2 --threads 4 "
            "--timeout 300 -b 0.0.0.0:8000 app.main:app --daemon --pid gunicorn.pid "
            "--error-logfile error.log --capture-output && sleep 3 && "
            "curl -s --max-time 5 http://127.0.0.1:8000/api/v1/health"
        )
    _in, out, _err = client.exec_command(cmd, timeout=60)
    print(out.read().decode("utf-8", "replace").strip())
    client.close()


def sync_app(password: str) -> None:
    targets = NODES
    print(f"Fast-syncing app/ directly to {len(targets)} nodes...")
    app_dir = REPO_ROOT / "app"
    
    # Collect files to upload (skip __pycache__ and compiled artifacts)
    files_to_sync = []
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith((".pyc", ".pyo")):
                local_path = Path(root) / f
                rel_path = local_path.relative_to(REPO_ROOT).as_posix()
                files_to_sync.append((local_path, rel_path))

    for node in targets:
        nid = node["node_id"]
        port = node["ssh_port"]
        print(f"[{nid}] syncing {len(files_to_sync)} files to port {port}...")
        for attempt in range(3):
            try:
                client = connect_with_retry(password, port, attempts=3)
                sftp = client.open_sftp()
                for local_file, rel in files_to_sync:
                    remote_file = f"/home/student/pond_catchment_backend/{rel}"
                    remote_dir = os.path.dirname(remote_file)
                    try:
                        sftp.mkdir(remote_dir)
                    except IOError:
                        pass
                    sftp.put(str(local_file), remote_file)
                sftp.close()
                cmd = "cd ~/pond_catchment_backend && kill -HUP $(cat gunicorn.pid) 2>/dev/null || true; sleep 1; curl -s --max-time 3 http://127.0.0.1:8000/api/v1/health"
                _, out, _ = client.exec_command(cmd, timeout=10)
                res = out.read().decode().strip()
                print(f"[{nid}] Reloaded OK: {res}")
                client.close()
                break
            except Exception as e:
                print(f"[{nid}] Attempt {attempt+1} failed: {e}")
                time.sleep(1.0)
    print("Fast sync completed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["bundle", "push", "sync", "nginx", "verify", "stop-node", "start-node"])
    parser.add_argument("ssh_port", nargs="?", type=int)
    args = parser.parse_args()
    password = os.environ["DEPLOY_SSH_PW"]

    if args.command == "bundle":
        build_bundle()
    elif args.command == "sync":
        sync_app(password)
    elif args.command == "push":
        commit = git_commit()
        bundle = build_bundle()
        targets = [n for n in NODES if args.ssh_port in (None, n["ssh_port"])]
        print(f"deploying commit {commit} to {len(targets)} node(s)...")
        for node in targets:
            deploy_node(password, node, bundle, commit)
        print("all targeted nodes deployed.")
    elif args.command == "nginx":
        install_nginx(password)
    elif args.command == "verify":
        verify(password)
    elif args.command in ("stop-node", "start-node"):
        if not args.ssh_port:
            raise SystemExit(f"{args.command} requires an SSH port")
        stop_start_node(password, args.ssh_port, args.command)


if __name__ == "__main__":
    main()
