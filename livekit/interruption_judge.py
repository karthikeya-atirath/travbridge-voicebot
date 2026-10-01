"""LLM second layer for barge-ins that got past the interruption guard.

The guard's first layer (filler/backchannel word lists + the embedding
redundancy check in semantic_check.py) is cheap but literal: anything with
"novel" content words still reaches the main LLM as a new turn, even when it
is only a reaction ("wow that sounds nice") that doesn't change what the
assistant should be saying. This module asks a small LLM a single yes/no
question — does this interruption change course? — and the guard uses the
answer to either cut the assistant off (RESPOND) or let it keep talking and
drop the utterance entirely (IGNORE).

Fails open: any timeout, error or unparseable answer counts as RESPOND, since
dropping real user content is worse than an unnecessary reply.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from enum import Enum

from google import genai
from google.genai import types

from app_logger import applog

JUDGE_MODEL = "gemini-3.5-flash-lite"
JUDGE_BASE_URL = "http://10.160.0.6:8000"
# The assistant's audio is held paused while the live path waits on this,
# so the budget has to stay short: past it we fall back to RESPOND. The
# proxy measured ~0.9-1.7s per call (no thinking-off option is accepted).
JUDGE_TIMEOUT_S = 2.0
_CACHE_SIZE = 32

JUDGE_PROMPT = """You are evaluating whether a user interruption during assistant speech requires the assistant to change course.

Assistant was speaking: {assistant_response}

User interruption: {user_interrupt}

Return ONLY one of:
RESPOND
IGNORE

Return RESPOND only if the interruption:
- changes the request
- corrects information
- adds constraints
- asks a new question
- expresses disagreement
- requests cancellation or modification

Return IGNORE for:
- acknowledgements
- backchanneling
- filler speech
- repeated information
- emotional reactions without new intent
- short confirmations like 'okay', 'yeah', 'right', 'mmhmm'

The user may speak Hindi, English or a mix of both. Do not explain your answer."""


class JudgeVerdict(str, Enum):
    RESPOND = "respond"
    IGNORE = "ignore"


class InterruptionJudge:
    def __init__(
        self,
        *,
        model: str = JUDGE_MODEL,
        base_url: str = JUDGE_BASE_URL,
        timeout: float = JUDGE_TIMEOUT_S,
        session_label: str = "session",
    ) -> None:
        self._client = genai.Client(
            vertexai=False,
            api_key="DummyAPIKey",
            http_options=types.HttpOptions(base_url=base_url),
        )
        self._model = model
        self._timeout = timeout
        self.session_label = session_label
        # The same utterance is often judged twice (live overlap, then again
        # when its final turn is dropped/committed) — don't pay for it twice.
        self._cache: OrderedDict[tuple[str, str], JudgeVerdict] = OrderedDict()

    async def judge(self, assistant_text: str, user_text: str) -> JudgeVerdict:
        assistant_text = assistant_text.strip()
        user_text = user_text.strip()
        if not user_text:
            return JudgeVerdict.IGNORE
        key = (assistant_text, user_text.lower())
        cached = self._cache.get(key)
        if cached is not None:
            applog.info(
                f"[LLM JUDGE][{self.session_label}] verdict={cached.value} cached=True "
                f"interruption={user_text!r}"
            )
            return cached

        start = time.perf_counter()
        raw = ""
        try:
            raw = await asyncio.wait_for(self._ask(assistant_text, user_text), self._timeout)
            verdict = _parse(raw)
            why = "parsed" if verdict is not None else "unparseable"
        except asyncio.TimeoutError:
            verdict, why = None, "timeout"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            verdict, why = None, f"error:{type(exc).__name__}"
        elapsed = time.perf_counter() - start

        if verdict is None:
            verdict = JudgeVerdict.RESPOND  # fail open
        else:
            self._cache[key] = verdict
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
        applog.info(
            f"[LLM JUDGE][{self.session_label}] verdict={verdict.value} ({why}) "
            f"latency={elapsed * 1000:.0f}ms raw={raw.strip()!r} interruption={user_text!r} "
            f"assistant={assistant_text[-120:]!r}"
        )
        return verdict

    async def _ask(self, assistant_text: str, user_text: str) -> str:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=JUDGE_PROMPT.format(
                assistant_response=assistant_text or "(nothing yet)",
                user_interrupt=user_text,
            ),
            config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=5),
        )
        return response.text or ""


def _parse(raw: str) -> JudgeVerdict | None:
    word = raw.strip().upper()
    if word.startswith("RESPOND"):
        return JudgeVerdict.RESPOND
    if word.startswith("IGNORE"):
        return JudgeVerdict.IGNORE
    return None
