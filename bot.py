"""Poseidon entrypoint for the Mech Royale game protocol."""

import json

import mechfight as mf
from strategy import Strategy


class Poseidon:
    name = "Poseidon"

    def __init__(self, hello: mf.Hello) -> None:
        self.strategy = Strategy(hello)
        caps = hello["config"].get("debug", {})
        self.debug_chars = int(caps.get("max_text_chars", 80))
        self.debug_bytes = int(caps.get("max_bytes", 8192))
        self.debug_remaining = int(caps.get("max_match_bytes", 8_000_000))

    def act(self, obs: mf.Observation) -> mf.Action:
        action, reason = self.strategy.decide(obs)
        if self.debug_chars > 0:
            label = reason[:self.debug_chars]
            drawing = {"label": label}
            # The transport escapes non-ASCII characters in compact JSON.
            size = len(json.dumps(drawing, separators=(",", ":")).encode("utf-8"))
            if size <= min(self.debug_bytes, self.debug_remaining):
                mf.draw.label(label)
                self.debug_remaining -= size
        return action


if __name__ == "__main__":
    mf.run(Poseidon)
