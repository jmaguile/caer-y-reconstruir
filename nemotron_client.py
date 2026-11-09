import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

NEMOTRON_MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/nvidia-nemotron-nano-9b-v2")
NEMOTRON_BASE_URL = os.getenv("NEMOTRON_BASE_URL", "https://integrate.api.nvidia.com/v1")
GEMMA_LOCAL_URL = os.getenv("GEMMA_LOCAL_URL", "http://localhost:8000/v1")

_nemotron = OpenAI(
    base_url=NEMOTRON_BASE_URL,
    api_key=os.environ.get("NVIDIA_API_KEY", "missing"),
)
_gemma = OpenAI(base_url=GEMMA_LOCAL_URL, api_key="not-needed")


def reason_nemotron(prompt: str, system: str = "", *, temperature: float = 0.2) -> str:
    """Reasoning agent call — goes to NVIDIA cloud Nemotron."""
    resp = _nemotron.chat.completions.create(
        model=NEMOTRON_MODEL,
        messages=[
            {"role": "system", "content": system or "You are a precise reasoning agent. When asked for JSON, output ONLY valid JSON, no prose."},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def reason_local(prompt: str, system: str = "", *, temperature: float = 0.2) -> str:
    """Cheap local Gemma calls for non-reasoning tasks. Falls back to Nemotron if local is unreachable."""
    try:
        resp = _gemma.chat.completions.create(
            model="gemma-4-26b",
            messages=[
                {"role": "system", "content": system or "Be concise."},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            timeout=15,
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return reason_nemotron(prompt, system, temperature=temperature)


def parse_json_blob(text: str) -> dict:
    """Extract a JSON object from LLM output that may have prose/markdown around it."""
    if not text:
        return {}
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    out = reason_nemotron("Reply with the single word: alive")
    print(out)
