"""Viewed media held for the cycle (state_spawn_context.md §1.15).

The checkpoint keeps a text note for each viewed image or video. The media
itself stays in memory here and is put back into what the model is sent.

An image is put back on every call, unchanged, until the cycle ends. Removing
it between two calls would change the history the provider has cached and
every later call would pay to write it again.

A video is sent once (`once=True`): it can be 200 MB, far too large to upload
again on every call of a cycle.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

# Above 20 images in one request Anthropic applies a stricter size rule to each.
MAX_HELD = 20
# What one held item is counted as in the trim budget (agent_harness VISION_PART_CHAR_BUDGET).
ITEM_CHARS = 16_000


def hold_limit(context_window_chars: int, item_chars: int = ITEM_CHARS) -> int:
    """How many viewed items may be held: a quarter of the budget, 1 to MAX_HELD."""
    if item_chars <= 0:
        return MAX_HELD
    fit = int(context_window_chars * 0.25) // item_chars
    return max(1, min(MAX_HELD, fit))


class ViewHold:
    def __init__(self, limit: int = MAX_HELD):
        self.limit = max(1, int(limit))
        self._parts: dict[str, list] = {}
        self._once: set[str] = set()

    def __len__(self) -> int:
        return len(self._parts)

    def hold(self, message_id: str, parts: list, once: bool = False) -> int:
        """Hold one viewed item. Returns how many older items became notes.

        Past the limit the oldest half go at once: one change to the history
        for several items, not one per item. `once`: the item is sent with the
        next model call only (`release_once`).
        """
        if not message_id:
            return 0
        self._parts[message_id] = list(parts)
        if once:
            self._once.add(message_id)
        if len(self._parts) <= self.limit:
            return 0
        keep = max(1, self.limit // 2)
        ids = list(self._parts)
        for old in ids[:-keep]:
            del self._parts[old]
            self._once.discard(old)
        return len(ids) - keep

    def release_once(self) -> int:
        """The model has replied: items held for one call become notes. Returns how many."""
        gone = [mid for mid in self._once if mid in self._parts]
        for mid in gone:
            del self._parts[mid]
        self._once.clear()
        return len(gone)

    def expand(self, messages: list) -> list:
        """The same messages, with each held note replaced by its media message."""
        if not self._parts:
            return messages
        out = []
        for message in messages:
            parts = self._parts.get(str(getattr(message, "id", "") or ""))
            if parts is not None and isinstance(message, HumanMessage):
                out.append(HumanMessage(content=parts, id=message.id))
            else:
                out.append(message)
        return out
