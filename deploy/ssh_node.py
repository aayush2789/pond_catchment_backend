"""SSH helper for deployment/inspection of the pond backend nodes.

Security: the SSH password is NEVER stored in this repository. It is read from
the DEPLOY_SSH_PW environment variable at invocation time.

Usage:
  python deploy/ssh_node.py <port> run  "<remote shell command>"
  python deploy/ssh_node.py <port> sudo "<remote command>"
  python deploy/ssh_node.py <port> put  <local_path> <remote_path>
"""
import os
import sys

import paramiko

HOST = "10.1.75.53"


def connect_with_retry(password: str, port: int, attempts: int = 6, backoff: float = 1.5):
    last_exc = None
    for i in range(attempts):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(HOST, port=port, username="student", password=password, timeout=10)
            return client
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            client.close()
            if i < attempts - 1:
                import time

                time.sleep(backoff)
    raise last_exc


def main():
    port = int(sys.argv[1])
    mode = sys.argv[2]
    password = os.environ["DEPLOY_SSH_PW"]

    client = connect_with_retry(password, port)

    if mode == "run":
        command = sys.argv[3]
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        print(out)
        if err.strip():
            print("--- STDERR ---")
            print(err)
        sys.exit(code)
    elif mode == "sudo":
        command = sys.argv[3]
        stdin, stdout, stderr = client.exec_command(
            f"sudo -S -p '' bash -c {repr(command)}", timeout=300
        )
        stdin.write(password + "\n")
        stdin.flush()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        print(out)
        if err.strip():
            print("--- STDERR ---")
            print(err)
        sys.exit(code)
    elif mode == "put":
        local, remote = sys.argv[3], sys.argv[4]
        sftp = client.open_sftp()
        sftp.put(local, remote)
        sftp.close()
        print(f"uploaded {local} -> {remote}")
    else:
        print(f"unknown mode {mode}")
        sys.exit(2)
    client.close()


if __name__ == "__main__":
    main()
