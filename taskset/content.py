"""
TaskItem content: the versioned, reviewable part of an item (docs/data-contract.md § TaskItem).

Content is a dict serialized canonically (sorted keys, NFC) so its sha256 identifies a
version. Kind-specific shape is checked here; status/split rules live in schema.sql.
"""
import hashlib
import json
import unicodedata
from functools import cache
from pathlib import Path

import yaml

from taskset.verifiers import validate_spec

KINDS = ("sft", "multi_turn", "preference", "reasoning", "rl_verifiable", "eval_mcq", "eval_open", "eval_code")
EVAL_KINDS = frozenset(k for k in KINDS if k.startswith("eval_"))
FLAGS = frozenset({"pii", "unsafe", "contaminated", "duplicate"})
ROLES = frozenset({"system", "user", "assistant"})
FIELDS = frozenset({"domain", "skills", "difficulty", "lang", "messages", "chosen", "rejected", "reference_answer",
                    "rubric", "choices", "answer_key", "verifier", "grounding", "meta"})
SPLIT_SALT = "llm-taskset/split/v1"
TAXONOMY_PATH = Path(__file__).with_name("taxonomy.yaml")


@cache
def taxonomy():
    return yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))


def _nfc(value):
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_nfc(v) for v in value]
    if isinstance(value, dict):
        return {k: _nfc(v) for k, v in value.items()}
    return value


def canonical(content):
    """(json text, sha256) of NFC content with sorted keys: equal content, equal hash."""
    text = json.dumps(_nfc(content), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_role(content):
    messages = content.get("messages") or []
    return messages[-1].get("role") if messages else None


def validate(kind, content):
    """Errors for `content` as an item of `kind` (empty list when valid)."""
    tax, errors = taxonomy(), []
    if kind not in KINDS:
        return [f"kind {kind!r} not in {KINDS}"]
    errors += [f"unknown field {k!r}" for k in content if k not in FIELDS]
    if content.get("domain") not in tax["domains"]:
        errors.append(f"domain {content.get('domain')!r} not in taxonomy v{tax['version']}")
    errors += [f"skill {s!r} not in taxonomy" for s in content.get("skills", []) if s not in tax["skills"]]
    if content.get("difficulty") is not None and content["difficulty"] not in tax["difficulty"]:
        errors.append(f"difficulty {content['difficulty']!r} not in {tax['difficulty']}")
    if not content.get("lang"):
        errors.append("lang is required")
    messages = content.get("messages")
    if not messages or any(m.get("role") not in ROLES or not str(m.get("content", "")).strip() for m in messages):
        errors.append("messages must be non-empty {role, content} with roles system/user/assistant")
        return errors
    users = sum(m["role"] == "user" for m in messages)
    if kind in ("sft", "reasoning", "multi_turn") and _last_role(content) != "assistant":
        errors.append(f"{kind} must end with an assistant message")
    if kind == "multi_turn" and users < 2:
        errors.append("multi_turn needs at least 2 user turns")
    if kind not in ("sft", "reasoning", "multi_turn") and _last_role(content) != "user":
        errors.append(f"{kind} prompt must end with a user message")
    if kind == "preference" and (not content.get("chosen") or not content.get("rejected")
                                 or content.get("chosen") == content.get("rejected")):
        errors.append("preference needs distinct chosen and rejected")
    if kind == "eval_mcq":
        choices = content.get("choices") or []
        if len(choices) < 2 or content.get("answer_key") not in "ABCDEFGHIJ"[:len(choices)]:
            errors.append("eval_mcq needs >= 2 choices and answer_key among their labels")
    if kind in ("rl_verifiable", "eval_open") and not (content.get("reference_answer") or content.get("rubric")):
        errors.append(f"{kind} needs reference_answer or rubric")
    verifier = content.get("verifier")
    if kind in ("rl_verifiable", "eval_code") and not verifier:
        errors.append(f"{kind} needs a verifier")
    if kind == "eval_code" and verifier and verifier.get("type") != "unit_test":
        errors.append("eval_code verifier must be unit_test")
    if verifier:
        errors += validate_spec(verifier)
    return errors


def prompt_key(content):
    """Text that fixes an item's split: the user turns, so paraphrased answers stay in one split."""
    return "\n".join(" ".join(m["content"].split()) for m in content["messages"] if m["role"] == "user")


def split_of(kind, content):
    """Training kinds: 90/5/5 train/dev/test by salted hash. Eval kinds: always test (holdout is chosen explicitly)."""
    if kind in EVAL_KINDS:
        return "test"
    digest = hashlib.sha256(f"{SPLIT_SALT}:{unicodedata.normalize('NFC', prompt_key(content))}".encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return "train" if bucket < 90 else "dev" if bucket < 95 else "test"
