# Changelog

All notable changes to `olab-voice` are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.1] - 2026-11-16

### Security
- The `nats` extra now requires `msgpack==1.2.4` (was `==1.2.1`, which is
  affected by a published msgpack advisory fixed in 1.2.3).

## [0.1.0] - 2026-10-06

First PyPI release. Local-first voice capture, STT, TTS, wake phrase, and
transport adapters.
