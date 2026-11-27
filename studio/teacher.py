"""
Teacher drafts through any OpenAI-compatible chat endpoint (local vLLM / llama.cpp server, or remote).

  LLM_STUDIO_TEACHER_URL    base URL up to /v1 (default http://127.0.0.1:8000/v1)
  LLM_STUDIO_TEACHER_MODEL  model name sent to the endpoint (default: the endpoint's first model)
  LLM_STUDIO_TEACHER_KEY    API key; else the Windows Credential Manager entry `llm-studio-teacher`
                            (cmdkey /generic:llm-studio-teacher /user:teacher /pass:<key>). Never logged.

A draft is only a proposal: the job stores it as a new version with origin model:<name> and the
item goes to pending, so a person accepts every teacher answer.
"""
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000/v1"
CRED_TARGET = "llm-studio-teacher"
TIMEOUT_S = 600
DRAFTABLE = ("sft", "multi_turn", "reasoning", "rl_verifiable", "eval_open")


def cred_read(target):
    """A generic Windows Credential Manager secret as `cmdkey` stores it (UTF-16LE); None elsewhere."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    cred = ctypes.POINTER(CREDENTIAL)()
    if not advapi.CredReadW(target, 1, 0, ctypes.byref(cred)):  # 1 = CRED_TYPE_GENERIC
        return None
    try:
        c = cred.contents
        return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
    finally:
        advapi.CredFree(cred)


def config():
    return {"url": os.environ.get("LLM_STUDIO_TEACHER_URL", DEFAULT_URL).rstrip("/"),
            "model": os.environ.get("LLM_STUDIO_TEACHER_MODEL") or None}


def _call(path, body=None):
    key = os.environ.get("LLM_STUDIO_TEACHER_KEY") or cred_read(CRED_TARGET)
    req = urllib.request.Request(config()["url"] + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"teacher trả lỗi HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"không gọi được teacher ở {config()['url']}: {e.reason}") from None


def model_name():
    name = config()["model"]
    return name or _call("/models")["data"][0]["id"]


def prompt_messages(kind, content):
    """The conversation the teacher must answer: everything up to the last user turn."""
    if kind not in DRAFTABLE:
        raise ValueError(f"teacher chưa nháp được loại {kind}")
    messages = list(content["messages"])
    while messages and messages[-1]["role"] == "assistant":
        messages.pop()
    if not messages or messages[-1]["role"] != "user":
        raise ValueError("cần ít nhất một lượt user để teacher trả lời")
    return messages


def apply_draft(kind, content, answer):
    """Content with the teacher's answer in the field that kind keeps answers in."""
    out = dict(content)
    if kind in ("rl_verifiable", "eval_open"):
        out["reference_answer"] = answer
    else:
        out["messages"] = prompt_messages(kind, content) + [{"role": "assistant", "content": answer}]
    return out


def draft(kind, content, model):
    reply = _call("/chat/completions", {"model": model, "messages": prompt_messages(kind, content),
                                        "temperature": 0.3})
    answer = (reply["choices"][0]["message"].get("content") or "").strip()
    if not answer:
        raise RuntimeError("teacher trả về câu trả lời rỗng")
    return apply_draft(kind, content, answer)
