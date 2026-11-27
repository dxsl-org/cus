"""
Runpod REST API v2 and SSH transport for lab/runpod_train.py.

The API key comes from RUNPOD_API_KEY or the Windows Credential Manager entry `runpod`
(`cmdkey /generic:runpod /user:runpod /pass:<key>`). SSH uses ~/.ssh/id_ed25519 against the pod's direct
endpoint (public IP + mapped port 22), the only Runpod SSH variant that carries file transfers. Files move as
tar streams over ssh stdin/stdout, so no scp/rsync is needed on Windows.
"""
import os
import pathlib
import subprocess
import tarfile
import time

import httpx

from lab import corpus
from studio.teacher import cred_read

API = "https://api.runpod.io/v2"
KEY = pathlib.Path.home() / ".ssh" / "id_ed25519"
KNOWN_HOSTS = corpus.LAB_DATA / "runpod" / "known_hosts"


def _client():
    key = os.environ.get("RUNPOD_API_KEY") or cred_read("runpod")
    if not key:
        raise SystemExit("no Runpod API key: set RUNPOD_API_KEY or `cmdkey /generic:runpod /user:runpod /pass:<key>`")
    return httpx.Client(base_url=API, headers={"Authorization": f"Bearer {key}"}, timeout=60)


def call(method, path, **kw):
    """One API request; raises SystemExit with Runpod's error detail on failure. Returns the JSON body or None."""
    with _client() as c:
        r = c.request(method, path, **kw)
    if r.status_code >= 400:
        raise SystemExit(f"Runpod {method} {path}: HTTP {r.status_code} {r.text[:500]}")
    return r.json() if r.content else None


def balance():
    return call("GET", "/billing/balance")["balanceAmount"]


def ensure_ssh_key():
    """Registers the local public key on the account if missing. PUT replaces the whole set, so keep the rest."""
    pub = KEY.with_suffix(".pub").read_text(encoding="utf-8").strip()
    keys = call("GET", "/account/ssh-keys")["keys"]
    if not any(k.split()[:2] == pub.split()[:2] for k in keys):
        call("PUT", "/account/ssh-keys", json={"keys": keys + [pub]})


def gpu_prices(cloud):
    """{catalog name: (gpu type id, $/h in cloud)}. Stock is not checked: the catalog lags, so callers try the
    create call directly and treat a capacity refusal as 'not now'."""
    return {g["name"]: (g["id"], g["price"][cloud.lower()]) for g in call("GET", "/catalog/gpus")["gpus"]
            if g["price"].get(cloud.lower())}


def create_pod(body):
    """POST /pods. Returns (pod, None), or (None, detail) when Runpod answers 400: no capacity for this GPU (the
    API gives no machine-readable code for it; a body that breaks a rule fails the same way on every GPU, so
    callers show the detail). Any other error exits, 402 (balance) included."""
    with _client() as c:
        r = c.post("/pods", json=body)
    if r.status_code == 400:
        return None, r.json().get("detail", r.text[:300])
    if r.status_code >= 400:
        raise SystemExit(f"Runpod POST /pods: HTTP {r.status_code} {r.text[:500]}")
    return r.json(), None


def wait_running(pod_id, timeout_s=1200, no_port_s=180):
    """Polls until the pod runs and exposes direct SSH; returns (host, port). A pod that runs without a public
    22/tcp mapping for no_port_s (a host without public IP) is billed but unusable, so give up early."""
    deadline, running_since = time.time() + timeout_s, None
    while time.time() < deadline:
        pod = call("GET", f"/pods/{pod_id}")
        direct = (pod.get("ssh") or {}).get("direct")
        if pod["status"] == "RUNNING" and direct:
            return direct["host"], direct["port"]
        if pod["status"] in ("ERROR", "TERMINATED"):
            raise SystemExit(f"pod {pod_id} is {pod['status']}")
        if pod["status"] == "RUNNING":
            running_since = running_since or time.time()
            if time.time() - running_since > no_port_s:
                raise SystemExit(f"pod {pod_id} runs without a public SSH port; `down --force`, then `up` again")
        print(f"  pod {pod['status'].lower()}...", flush=True)
        time.sleep(15)
    raise SystemExit(f"pod {pod_id} not reachable after {timeout_s // 60} min; check the console, then `down --force`")


class Shell:
    """Commands and tar transfers on one pod over direct SSH."""

    def __init__(self, host, port):
        KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
        self.base = ["ssh", "-p", str(port), "-i", str(KEY), "-o", "IdentitiesOnly=yes",
                     "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
                     "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=20", f"root@{host}"]

    def run(self, script, check=True, capture=False):
        """Runs a bash script on the pod (stdin-free); returns stdout when capture, else the exit code."""
        r = subprocess.run(self.base + ["bash", "-lc", _quote(script)], capture_output=capture, text=capture)
        if check and r.returncode:
            raise SystemExit(f"remote command failed ({r.returncode}): {script.splitlines()[0][:120]}"
                             + (f"\n{r.stderr[-2000:]}" if capture else ""))
        return r.stdout if capture else r.returncode

    def wait_ssh(self, timeout_s=300):
        deadline = time.time() + timeout_s
        while self.run("true", check=False) != 0:
            if time.time() > deadline:
                raise SystemExit("sshd on the pod did not answer")
            time.sleep(10)

    def sizes(self, remote_dir):
        """{relative path: size} of the files under remote_dir (empty when it does not exist)."""
        out = self.run(f"cd {remote_dir} 2>/dev/null && find . -type f -printf '%P\\t%s\\n' || true", capture=True)
        return {p: int(s) for p, s in (line.split("\t") for line in out.splitlines() if line)}

    def put(self, files, remote_dir):
        """Streams {arcname: local path} into remote_dir as one tar archive."""
        p = subprocess.Popen(self.base + [f"mkdir -p {remote_dir} && tar -xf - -C {remote_dir}"], stdin=subprocess.PIPE)
        with tarfile.open(fileobj=p.stdin, mode="w|") as tar:
            for arc, path in files.items():
                tar.add(path, arcname=arc, recursive=False)
        p.stdin.close()
        if p.wait():
            raise SystemExit(f"upload to {remote_dir} failed")

    def get(self, remote_dir, names, local_dir):
        """Streams the named files of remote_dir into local_dir; each lands as .part first, then is renamed."""
        local_dir.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen(self.base + [f"tar -cf - -C {remote_dir} {' '.join(names)}"], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=p.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                part = local_dir / (pathlib.Path(member.name).name + ".part")
                with open(part, "wb") as f:
                    src = tar.extractfile(member)
                    while chunk := src.read(1 << 22):
                        f.write(chunk)
                part.replace(local_dir / pathlib.Path(member.name).name)
                print(f"  {member.name} {member.size / 1e9:.2f} GB", flush=True)
        if p.wait():
            raise SystemExit(f"download from {remote_dir} failed")


def _quote(script):
    return "'" + script.replace("'", "'\"'\"'") + "'"
