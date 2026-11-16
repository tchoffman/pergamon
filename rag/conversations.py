"""Short-term memory so follow-up questions make sense.

"How do I build a wood-fired oven?" → "A bread oven" only works if the
second message is read with the first. Each conversation (one radio, or one
browser tab) keeps its last few exchanges, in memory only, and is forgotten
after a quiet spell or when the person starts over.

Only the question and the final answer are kept, not tool results, so the
history stays small enough for the model's context and a 16 GB machine.
"""
from __future__ import annotations

import threading
import time

MAX_TURNS = 6           # exchanges remembered per conversation
IDLE_S = 30 * 60        # forget after this long without a message
MAX_CONVERSATIONS = 500  # oldest dropped first; protects memory on a busy hub
ANSWER_CHARS = 1500     # long answers are trimmed in memory, not on screen


class Conversations:
    def __init__(self, max_turns: int = MAX_TURNS, idle_s: float = IDLE_S):
        self.max_turns, self.idle_s = max_turns, idle_s
        self._turns: dict[str, list[tuple[str, str]]] = {}
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        for key in [k for k, t in self._seen.items() if now - t > self.idle_s]:
            self._turns.pop(key, None)
            self._seen.pop(key, None)
        while len(self._seen) > MAX_CONVERSATIONS:
            oldest = min(self._seen, key=self._seen.get)
            self._turns.pop(oldest, None)
            self._seen.pop(oldest, None)

    def history(self, key: str, now: float | None = None) -> list[dict]:
        """Earlier exchanges as chat messages, oldest first."""
        now = now or time.time()
        with self._lock:
            self._expire(now)
            turns = list(self._turns.get(key, []))
        msgs: list[dict] = []
        for q, a in turns:
            msgs += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        return msgs

    def add(self, key: str, question: str, answer: str, now: float | None = None) -> None:
        now = now or time.time()
        with self._lock:
            turns = self._turns.setdefault(key, [])
            turns.append((question, answer[:ANSWER_CHARS]))
            del turns[:-self.max_turns]
            self._seen[key] = now
            self._expire(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._turns.pop(key, None)
            self._seen.pop(key, None)


# One store per process: the web app and the radio listener run separately.
store = Conversations()
