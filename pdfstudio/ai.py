"""AI reading of hard pages (OCR engine "ai"): Bengali with math, handwriting, complex tables, poor scans.

Each page is sent as a picture to the model the person chose, with their own API key:
  - Claude (Anthropic): through the official `anthropic` SDK (optional: pip install "phi-pdf-studio[ai]");
  - Gemini (Google, has a free tier): through Google's REST API (no extra package).
The key comes with the request (typed in Settings, kept in the person's browser, never stored by the server) or,
on a computer where the owner allows it, from the environment (ANTHROPIC_API_KEY / GEMINI_API_KEY).
The reply is Markdown: headings, lists, tables as Markdown tables, math as LaTeX in $…$.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf

from . import tools as T
from .tools import Input, Progress, Result, ToolError

PROVIDERS = {"claude": "Claude (Anthropic)", "gemini": "Gemini (Google)"}
MODELS = {
    "claude": {"claude-opus-5-5": "Claude Opus 5.5 (best)", "claude-sonnet-5-5": "Claude Sonnet 5.5 (faster, cheaper)",
               "claude-haiku-4-5": "Claude Haiku 4.5 (cheapest)"},
    "gemini": {"gemini-3.5-flash": "Gemini 3.5 Flash (free tier)", "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite "
               "(free tier, fastest)", "gemini-3.1-pro-preview": "Gemini 3.1 Pro (paid)"},
}
ENV_KEYS = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}
USE_SERVER_KEYS = False          # the app turns this on for a private computer (never for a public server)
MAX_AI_PAGES = 300
PROMPT = """Transcribe everything written on this page image, exactly as written, in its own language and script
(for example Bengali, English, Hindi, Arabic). Do not translate, summarise, correct or add anything.
Format the result as Markdown:
- keep headings, numbered questions, lists and paragraphs in reading order (columns left to right);
- tables as Markdown tables;
- every mathematical expression, formula, unit with exponent or chemical equation in LaTeX between $…$
  (or $$…$$ for a formula on its own line);
- a picture or diagram as one line: [Figure: short description].
Reply with the transcription only, no remarks before or after it."""


class AIError(ToolError):
    pass


def key_for(provider: str, given: str) -> str:
    key = (given or "").strip()
    if not key and USE_SERVER_KEYS:
        key = os.environ.get(ENV_KEYS[provider], "").strip()
    if not key:
        raise AIError(f"Add your {PROVIDERS[provider]} API key in Settings → AI reading first.")
    return key


# ---------------------------------------------------------------------------------------------- Claude
def _claude(png: bytes, model: str, key: str, extra: str) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise AIError("Reading with Claude needs the anthropic package: pip install \"phi-pdf-studio[ai]\"") from exc
    client = anthropic.Anthropic(api_key=key, max_retries=3, timeout=300)
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(png).decode("ascii")}},
               {"type": "text", "text": PROMPT + (f"\nAlso: {extra}" if extra else "")}]
    try:
        # A page declined by a safety check is re-run on another model inside the same call (server-side fallback).
        response = client.beta.messages.create(model=model, max_tokens=16000, betas=["server-side-fallback-2026-07-01"],
                                               fallbacks="default", messages=[{"role": "user", "content": content}])
    except anthropic.AuthenticationError as exc:
        raise AIError("Claude did not accept the API key. Check it in Settings → AI reading.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIError("This Claude API key may not use that model.") from exc
    except anthropic.NotFoundError as exc:
        raise AIError(f"Claude does not know the model “{model}”.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Claude says too many requests right now: wait a minute and try again.") from exc
    except anthropic.BadRequestError as exc:
        text = str(getattr(exc, "message", exc))
        if "credit" in text.lower() or "billing" in text.lower():
            raise AIError("The Claude account has no credit left (add credit at console.anthropic.com).") from exc
        raise AIError(f"Claude refused the request: {text[:300]}") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Claude is not answering properly right now ({exc.status_code}); try again later.") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Could not reach Claude: check the internet connection.") from exc
    if response.stop_reason == "refusal":
        return "[This page was not transcribed: the AI declined it.]"
    return "".join(block.text for block in response.content if block.type == "text").strip()


# ---------------------------------------------------------------------------------------------- Gemini
def _gemini(png: bytes, model: str, key: str, extra: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"role": "user", "parts": [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}},
        {"text": PROMPT + (f"\nAlso: {extra}" if extra else "")}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 16000}}
    data = json.dumps(body).encode("utf-8")
    for attempt in range(4):
        request = urllib.request.Request(url, data=data, method="POST",
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                reply = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            if exc.code in (429, 500, 503) and attempt < 3 and "PerDay" not in detail:
                time.sleep(4 * (attempt + 1) ** 2)
                continue
            if exc.code in (400, 403) and ("API key" in detail or "API_KEY" in detail):
                raise AIError("Gemini did not accept the API key. Check it in Settings → AI reading.") from exc
            if exc.code == 404:
                raise AIError(f"Gemini does not know the model “{model}”.") from exc
            if exc.code == 429:
                raise AIError("Gemini's free allowance for today is used up (or too many requests): try later or "
                              "another model.") from exc
            if exc.code == 402:
                raise AIError("The Gemini project has no credit: use a free-tier key or add billing.") from exc
            message = re.search(r'"message":\s*"([^"]+)"', detail)
            raise AIError(f"Gemini answered {exc.code}: {message.group(1) if message else detail[:200]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(3)
                continue
            raise AIError("Could not reach Gemini: check the internet connection.") from exc
    candidates = reply.get("candidates") or []
    if not candidates:
        reason = (reply.get("promptFeedback") or {}).get("blockReason", "no answer")
        return f"[This page was not transcribed: {reason}.]"
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


def transcribe(png: bytes, provider: str, model: str, key: str, extra: str = "") -> str:
    text = (_claude if provider == "claude" else _gemini)(png, model, key, extra)
    return re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", text.strip())


# ---------------------------------------------------------------------------------------------- the tool
def ai_read(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    provider = T._choice(p, "ai_provider", tuple(PROVIDERS), "gemini")
    model = T._text(p, "ai_model", 80).strip() or next(iter(MODELS[provider]))
    if not re.fullmatch(r"[a-zA-Z0-9._-]{3,80}", model):
        raise AIError("That model name is not valid.")
    key = key_for(provider, T._text(p, "ai_key", 400))
    extra = T._text(p, "ai_note", 600).strip()
    output = T._choice(p, "output", ("md", "txt", "docx"), "md")
    if output not in ("md", "txt", "docx"):
        output = "md"
    inp = inputs[0]
    doc = T.open_pdf(inp)
    pages = T.page_set(T._text(p, "pages", 4000), doc.page_count)
    if len(pages) > MAX_AI_PAGES:
        doc.close()
        raise AIError(f"AI reading takes at most {MAX_AI_PAGES} pages at a time: choose the pages.")
    dpi = T._int(p, "dpi", 200, 100, 300)
    pictures = []
    for i in pages:
        page = T._upright(doc[i])
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        if pix.width * pix.height > 30_000_000:
            pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
        pictures.append((i, pix.tobytes("png")))
    doc.close()
    results: dict[int, str] = {}
    progress(0, len(pictures), "sending pages to the AI")
    with ThreadPoolExecutor(max_workers=2 if provider == "gemini" else 4) as pool:
        futures = {pool.submit(transcribe, png, provider, model, key, extra): i for i, png in pictures}
        for n, future in enumerate(futures, 1):
            i = futures[future]
            results[i] = future.result()
            progress(n, len(pictures), f"page {i + 1}")
    ordered = [(i + 1, results[i]) for i in pages]
    from .ocr import _docx
    if output == "docx":
        out = T._file_out(out_dir, _docx(ordered, inp.stem), T._name(inp.stem, "AI text", ".docx"))
    else:
        body = "".join(f"\n\n<!-- page {n} -->\n\n{t}" if output == "md" else f"\n\n===== Page {n} =====\n\n{t}"
                       for n, t in ordered).strip() + "\n"
        out = T._file_out(out_dir, body.encode("utf-8"), T._name(inp.stem, "AI text", f".{output}"))
    chars = sum(len(t) for _, t in ordered)
    return Result([out], f"{len(pages)} page(s) read by {MODELS[provider].get(model, model)}: {chars:,} characters.",
                  {"pages": len(pages), "chars": chars, "model": model})
