"""
adapters.py — model adapters + prompt construction for the En→Ko benchmark.

Every adapter is a callable: adapter(system: str, user: str) -> str  (returns Korean text).
SDK/network imports are deferred so importing this module never fails without keys.

Spec format passed on the CLI:  "<provider>:<model_id>"
  passthrough                      -> returns the source unchanged (pipeline smoke test)
  reference                        -> returns the dataset reference_ko (scoring sanity check)
  anthropic:claude-opus-4-8        -> Anthropic Messages API     (ANTHROPIC_API_KEY)
  openai:gpt-5.5                   -> OpenAI Chat Completions     (OPENAI_API_KEY)
  gemini:gemini-2.5-pro            -> Google Gemini              (GEMINI_API_KEY)
  papago                           -> Naver Cloud Papago NMT     (NCP_CLIENT_ID / NCP_CLIENT_SECRET)
  hyperclova:HCX-007               -> Naver CLOVA Studio         (CLOVA_API_KEY)
  hf:ModelSpace/GemmaX2-28-9B-v0.1 -> local transformers         (needs torch+transformers+GPU)
"""

from __future__ import annotations
import json
import os
import pathlib

SKILL_DIR = pathlib.Path(__file__).resolve().parents[2] / "skills" / "korean-translation"
SYSTEM_PROMPT_PATH = SKILL_DIR / "assets" / "system_prompt.ko.txt"

_REGISTER_HINT = {
    "noun": "명사형(버튼/레이블), 마침표 없음",
    "haeyo": "해요체(~하세요)",
    "hapsyo": "합쇼체(~합니다/~됩니다)",
    "hada": "한다체/평서체(~한다/~이다)",
    "progress": "진행 표시, 말줄임표(…) 사용",
}


def load_system_prompt() -> str:
    try:
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ("You are a professional English→Korean software localization translator. "
                "Produce natural ko-KR, avoid 번역투, keep placeholders unchanged.")


def build_translate_prompt(entry: dict, use_skill: bool = True) -> tuple[str, str]:
    """Return (system, user) messages for the Pass-1 translation."""
    if not use_skill:
        # naive baseline: minimal instruction, no scaffold
        return ("You are a translator.",
                f"Translate the following English UI text to Korean:\n{entry['source_en']}")
    system = load_system_prompt()
    blocks = []
    reg = entry.get("register")
    if reg in _REGISTER_HINT:
        blocks.append(f"문체: {_REGISTER_HINT[reg]}")
    g = entry.get("glossary")
    if g:
        terms = "\n".join(f"- {k} → {v}" for k, v in g.items())
        blocks.append("반드시 다음 용어를 사용하세요(필요한 조사·어미만 붙임):\n" + terms)
    ph = entry.get("placeholders")
    if ph:
        blocks.append("다음 항목은 그대로 유지(개수·내용 변경 금지, 위치만 조정 가능): " + ", ".join(ph))
    user = ""
    if blocks:
        user += "\n".join(blocks) + "\n\n"
    user += f"다음 영어 제품 문구를 한국어로 번역하세요:\n{entry['source_en']}"
    return system, user


def build_polish_prompt(entry: dict, draft: str) -> tuple[str, str]:
    """Return (system, user) messages for the Pass-2 native-polish step."""
    system = load_system_prompt()
    reg = _REGISTER_HINT.get(entry.get("register", ""), "원문 맥락에 맞는 일관된 문체")
    user = (
        "다음 한국어 번역을 한국어 원어민 제품 작가가 처음부터 쓴 것처럼 자연스럽게 다듬으세요.\n"
        "능동태 우선, 자명한 대명사·주어 생략, 불필요한 '들' 제거, have는 '있다', "
        "문장 첫머리 '그리고/그러나/그래서' 금지(연결어미 사용), 긴 문장은 분할.\n"
        f"문체: {reg}로 통일. 플레이스홀더와 영문 고유명사는 그대로. 의미 변경 금지. 다듬은 결과만 출력.\n\n"
        f"원문(영어): {entry['source_en']}\n"
        f"초벌 번역(한국어): {draft}"
    )
    return system, user


# --------------------------------------------------------------------------- #
#  Adapters                                                                     #
# --------------------------------------------------------------------------- #
class Passthrough:
    """Returns source unchanged — lets you exercise run/score with no API key."""
    def __init__(self, _model=None):
        self._dataset = {}

    def __call__(self, system, user):
        # crude: pull the last line (the source) back out
        return user.strip().splitlines()[-1]


class ReferenceEcho:
    """Returns the gold reference for an entry (sanity check: should score ~ceiling)."""
    def __init__(self, _model=None):
        self.current_reference = ""

    def __call__(self, system, user):
        return self.current_reference


class Anthropic:
    def __init__(self, model):
        self.model = model

    def __call__(self, system, user):
        import anthropic  # pip install anthropic
        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        msg = client.messages.create(
            model=self.model, max_tokens=1024, temperature=0.2,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content
                       if getattr(b, "type", "") == "text").strip()


class OpenAIChat:
    def __init__(self, model):
        self.model = model

    def __call__(self, system, user):
        from openai import OpenAI  # pip install openai
        client = OpenAI()  # reads OPENAI_API_KEY
        r = client.chat.completions.create(
            model=self.model, temperature=0.2,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return (r.choices[0].message.content or "").strip()


class Gemini:
    def __init__(self, model):
        self.model = model

    def __call__(self, system, user):
        from google import genai  # pip install google-genai
        from google.genai import types
        client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
        r = client.models.generate_content(
            model=self.model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.2),
        )
        return r.text.strip()


class Papago:
    """Naver Cloud Platform Papago NMT. Note: 'system' is ignored (NMT, not an LLM)."""
    def __init__(self, _model=None):
        pass

    def __call__(self, system, user):
        import urllib.request
        import urllib.parse
        cid = os.environ["NCP_CLIENT_ID"]
        secret = os.environ["NCP_CLIENT_SECRET"]
        text = user.strip().splitlines()[-1]
        data = urllib.parse.urlencode({"source": "en", "target": "ko", "text": text}).encode()
        req = urllib.request.Request(
            "https://naveropenapi.apigw.ntruss.com/nmt/v1/translation", data=data,
            headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["message"]["result"]["translatedText"].strip()


class HyperClova:
    """Naver CLOVA Studio chat completions (HCX). Endpoint/host may vary by tenant."""
    def __init__(self, model="HCX-007"):
        self.model = model

    def __call__(self, system, user):
        import urllib.request
        key = os.environ["CLOVA_API_KEY"]
        host = os.environ.get("CLOVA_HOST", "https://clovastudio.stream.ntruss.com")
        body = json.dumps({
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2, "maxTokens": 1024,
        }).encode()
        req = urllib.request.Request(
            f"{host}/testapp/v1/chat-completions/{self.model}", data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)["result"]["message"]["content"].strip()


class HFLocal:
    """Local open-weight model via transformers (e.g. GemmaX2, Hunyuan-MT). Lazy-loaded."""
    _cache = {}

    def __init__(self, model):
        self.model = model

    def __call__(self, system, user):
        import torch  # noqa
        from transformers import pipeline
        if self.model not in self._cache:
            self._cache[self.model] = pipeline("text-generation", model=self.model,
                                               device_map="auto", torch_dtype="auto")
        gen = self._cache[self.model]
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        out = gen(msgs, max_new_tokens=512, do_sample=False)
        return out[0]["generated_text"][-1]["content"].strip()


class Ollama:
    """Local open-weight model via Ollama — fully offline. e.g. `ollama pull hf.co/...`.
    Spec: ollama:<model-tag>  (OLLAMA_HOST overridable, default http://localhost:11434)."""
    def __init__(self, model):
        self.model = model or "qwen2.5"

    def __call__(self, system, user):
        import urllib.request
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False, "options": {"temperature": 0.2},
        }).encode()
        req = urllib.request.Request(f"{host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.load(resp)["message"]["content"].strip()


class CodexCLI:
    """Codex CLI via `codex exec` — uses your existing Codex login (NO API key).
    Spec: codex  or  codex:<model>. Works for Korean but is coding-tuned — lean on the post-edit
    gate. `codex exec` can emit log lines; we return the last non-empty stdout line."""
    def __init__(self, model=None):
        self.model = model

    def __call__(self, system, user):
        import subprocess
        prompt = f"{system}\n\n---\n{user}\n\n(Output ONLY the Korean translation, nothing else.)"
        cmd = ["codex", "exec", prompt]
        if self.model:
            cmd += ["-m", self.model]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"`codex exec` failed: {(r.stderr or r.stdout).strip()[:200]}")
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        return (lines[-1] if lines else "").strip()


_PROVIDERS = {
    "passthrough": Passthrough, "reference": ReferenceEcho, "anthropic": Anthropic,
    "openai": OpenAIChat, "gemini": Gemini, "papago": Papago,
    "hyperclova": HyperClova, "hf": HFLocal, "ollama": Ollama,
    "codex": CodexCLI,
}


def get_adapter(spec: str):
    """Build an adapter from a '<provider>:<model>' spec."""
    provider, _, model = spec.partition(":")
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}. Options: {sorted(_PROVIDERS)}")
    return _PROVIDERS[provider](model or None)
