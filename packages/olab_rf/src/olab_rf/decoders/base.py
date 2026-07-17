from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from olab_rf.models import Observation, Track


@dataclass(slots=True)
class DecodedMessage:
    observation: Observation
    track: Track | None = None


class Decoder:
    mode: str

    def messages(self) -> Iterator[DecodedMessage]:
        raise NotImplementedError
