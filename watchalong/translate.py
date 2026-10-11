"""Translation of the streamer's lines, behind a provider interface.

Working providers:
- "none" passes the Japanese transcript through untouched.
- "claude-code" runs the Claude Code CLI headless (`claude -p`), so it's covered by a Claude
  Pro/Max subscription instead of API billing.
The API-backed providers are stubs to fill in.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .util import fmt_time, log


@dataclass
class TranslationContext:
    stream_title: str = ""
    # Anime dialogue on screen around these lines, [(stream_time, text)]. Gives the model
    # the scene the streamer is reacting to, which matters a lot for short reactions.
    anime_dialogue: list[tuple[float, str]] = field(default_factory=list)
    target_language: str = "English"
    # Names and terms, [(japanese, english)]; the English side may be empty.
    glossary: list[tuple[str, str]] = field(default_factory=list)


class Translator:
    name = "base"

    def cache_key(self) -> str:
        """Anything that changes the output (model, prompt), so stale caches aren't reused."""
        return self.name

    def translate(self, lines: list[dict], context: TranslationContext) -> list[str]:
        """lines: [{"start", "end", "text"}] -> one translated string per line, same order."""
        raise NotImplementedError


class PassthroughTranslator(Translator):
    name = "none"

    def translate(self, lines, context):
        return [line["text"] for line in lines]


_SYSTEM = """You translate live-stream subtitles. The input is an automatic Japanese \
transcript of a VTuber talking while she watches an anime with her viewers. Speech \
recognition makes mistakes: misheard words, wrong kanji, names spelled phonetically. Use the \
anime dialogue on screen at the same moment to work out what she actually meant and what she \
is reacting to, and use the anime's official English names for characters, places and terms.

Write natural, casual {lang} subtitles that keep her personality and humour. Keep them short \
enough to read at a glance. If a line is pure noise, filler, or a hallucination of the speech \
recognizer (e.g. repeated syllables, "thanks for watching"), translate it as an empty string.

Reply with only a JSON array: one string per input line, same order, exactly {n} strings."""


class ClaudeCodeTranslator(Translator):
    """Uses the `claude` CLI in print mode. Auth: an existing `claude` login, or a
    CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` (needed inside Docker)."""
    name = "claude-code"
    batch = 100        # lines per request
    overlap = 10       # earlier lines repeated as context, not re-translated
    parallel = 4
    attempts = 3

    def __init__(self):
        if not shutil.which("claude"):
            raise SystemExit("the claude-code translator needs the `claude` CLI on PATH")
        self.model = os.environ.get("WATCHALONG_CLAUDE_MODEL")  # default: the CLI's default

    def cache_key(self):
        return f"{self.name}|{self.model}|{_SYSTEM}|{self.batch}|{self.overlap}"

    def translate(self, lines, context):
        batches = [(i, min(i + self.batch, len(lines))) for i in range(0, len(lines), self.batch)]
        out: list[str] = [""] * len(lines)

        def work(k):
            a, b = batches[k]
            out[a:b] = self._batch(lines, a, b, context)
            log.info("translated batch %d/%d (%s)", k + 1, len(batches), fmt_time(lines[a]["start"]))

        with ThreadPoolExecutor(self.parallel) as ex:
            list(ex.map(work, range(len(batches))))
        return out

    def _batch(self, lines, a, b, context) -> list[str]:
        n = b - a
        t0, t1 = lines[a]["start"] - 20, lines[b - 1]["end"] + 5
        times = [t for t, _ in context.anime_dialogue]
        anime = context.anime_dialogue[bisect_left(times, t0):bisect_right(times, t1)]
        prompt = [f"Stream title: {context.stream_title}" if context.stream_title else ""]
        if context.glossary:
            prompt.append("Glossary (names and terms; the transcript may spell them differently):")
            prompt += [f"{ja} = {en}" if en else ja for ja, en in context.glossary]
        if a:
            prompt.append("Earlier lines, for context only (do not translate):")
            prompt += [f"[{fmt_time(x['start'])}] {x['text']}" for x in lines[max(0, a - self.overlap):a]]
        if anime:
            prompt.append("\nAnime dialogue on screen during these lines (official English subs):")
            prompt += [f"[{fmt_time(t)}] {text}" for t, text in anime]
        else:
            prompt.append("\n(No anime is playing during these lines; she is chatting with viewers.)")
        prompt.append(f"\nTranslate these {n} lines:")
        prompt += [f"{i + 1}. [{fmt_time(x['start'])}] {x['text']}" for i, x in enumerate(lines[a:b])]

        system = _SYSTEM.format(lang=context.target_language, n=n)

        def parse(text):
            texts = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
            if len(texts) != n or not all(isinstance(t, str) for t in texts):
                raise ValueError(f"got {len(texts)} lines, wanted {n}")
            return [t.strip() for t in texts]

        return self._ask(system, "\n".join(prompt), parse, f"batch at {fmt_time(lines[a]['start'])}")

    def _ask(self, system: str, prompt: str, parse, what: str):
        cmd = ["claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
               "--no-session-persistence", "--system-prompt", system]
        if self.model:
            cmd += ["--model", self.model]
        error = ""
        for attempt in range(self.attempts):
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
            try:
                result = json.loads(proc.stdout)
                if result.get("is_error"):
                    raise ValueError(result.get("result"))
                return parse(result["result"])
            except (ValueError, AttributeError, KeyError, TypeError) as e:
                error = str(e) or proc.stderr.strip()[-500:]
            log.warning("claude-code %s failed (%s), retrying", what, error[:200])
        raise SystemExit(f"claude-code {what} failed: {error}\n{proc.stderr.strip()[-1000:]}")

    def suggest_vocab(self, context: TranslationContext) -> list[tuple[str, str]]:
        """Names, places and recurring terms of the anime, in Japanese and English, read
        off the anime's English subtitles. Used as speech-recognition hints and glossary."""
        seen, sample = set(), []
        for _, text in context.anime_dialogue:
            if text not in seen:
                seen.add(text)
                sample.append(text)
        sample = "\n".join(sample)[:60000]

        def parse(text):
            items = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
            return [(i["ja"].strip(), i.get("en", "").strip()) for i in items
                    if isinstance(i, dict) and str(i.get("ja", "")).strip()]

        return self._ask(_VOCAB_SYSTEM, f"Stream title: {context.stream_title}\n\n"
                         f"Anime English subtitles:\n{sample}", parse, "vocabulary")


_VOCAB_SYSTEM = """You help translate a speech-recognized transcript of a VTuber's stream while \
she watches an anime. From the stream title and the anime's English subtitles, list the \
names and words she is likely to say that a recognizer would get wrong: character names, \
places, special terms of the anime, and the anime's title. Also include the streamer's own \
name and fan name if the title shows who she is. Give each as it is written in Japanese \
(the official Japanese spelling, usually katakana for names) and in English as the \
subtitles write it. At most 40 items, most important first. Reply with only a JSON array \
of {"ja": "...", "en": "..."} objects."""


class _NotImplementedTranslator(Translator):
    env_key = ""

    def translate(self, lines, context):
        raise NotImplementedError(
            f"the '{self.name}' translator is a placeholder and isn't implemented yet "
            f"(it will read {self.env_key} from .env). Use --translator none for now.")


class AnthropicTranslator(_NotImplementedTranslator):
    name = "anthropic"
    env_key = "ANTHROPIC_API_KEY"


class OpenAITranslator(_NotImplementedTranslator):
    name = "openai"
    env_key = "OPENAI_API_KEY"


class GeminiTranslator(_NotImplementedTranslator):
    name = "gemini"
    env_key = "GEMINI_API_KEY"


TRANSLATORS: dict[str, type[Translator]] = {
    t.name: t for t in (PassthroughTranslator, ClaudeCodeTranslator, AnthropicTranslator, OpenAITranslator, GeminiTranslator)
}


def get_translator(name: str) -> Translator:
    return TRANSLATORS[name]()


_TAG = re.compile(r"\{[^}]*\}")


def anime_dialogue(timeline, episode_subs: list[Path]) -> list[tuple[float, str]]:
    """The anime's subtitle lines placed in stream time, [(stream_time, text)], sorted."""
    per_episode = []
    for path in episode_subs:
        lines = []
        if path and path.exists():
            for row in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not row.startswith("Dialogue:"):
                    continue
                f = row.split(",", 9)
                if len(f) < 10 or "\\p1" in f[9] or "\\pos" in f[9]:  # drawings, typeset signs
                    continue
                text = _TAG.sub("", f[9]).replace("\\N", " ").replace("\\n", " ").strip()
                if text:
                    lines.append((_ass_time(f[1]), text))
        per_episode.append(sorted(set(lines)))
    out = []
    for iv in timeline:
        if iv.kind != "play":
            continue
        for t, text in per_episode[iv.episode]:
            st = iv.start + (t - iv.anime_t)
            if iv.start <= st < iv.end:
                out.append((st, text))
    return sorted(out)


def _ass_time(s: str) -> float:
    h, m, sec = s.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)
