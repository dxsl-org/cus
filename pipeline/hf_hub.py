"""
Publishing to the Hugging Face Hub (organization DXSL): token lookup, card rendering, folder upload.

The token comes from HF_TOKEN or the Windows Credential Manager entry `huggingface`
(`cmdkey /generic:huggingface /user:DXSL /pass:<token>`); it needs write access to the DXSL organization.
"""
import os
import subprocess

from pipeline.paths import ROOT
from studio.teacher import cred_read


def git_commit():
    """HEAD of this repository: every card links the exact code that produced the upload."""
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def render_card(template, out_dir, **fields):
    """Writes the dataset card README.md from a template whose {name} placeholders are filled from fields."""
    text = template.read_text(encoding="utf-8")
    for key, value in fields.items():
        text = text.replace("{" + key + "}", str(value))
    (out_dir / "README.md").write_text(text, encoding="utf-8")


def folder_size(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def upload_dataset(repo_id, folder, delete_patterns, message):
    """Creates the public dataset repo if needed and mirrors `folder` into it: remote files matching
    delete_patterns and absent locally are removed, so a re-run replaces the previous upload."""
    from huggingface_hub import HfApi

    token = os.environ.get("HF_TOKEN") or cred_read("huggingface")
    if not token:
        raise SystemExit("no Hugging Face token: set HF_TOKEN or `cmdkey /generic:huggingface /user:DXSL /pass:<token>`")
    api = HfApi(token=token)
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True)
    api.upload_folder(repo_id=repo_id, repo_type="dataset", folder_path=folder, delete_patterns=delete_patterns,
                      commit_message=message)
    return f"https://huggingface.co/datasets/{repo_id}"
