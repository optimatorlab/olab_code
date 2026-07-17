# Web Demo Boundary

The bundled FastAPI/static app is a demo and test surface for `olab_rf`. It is not the primary product and should not become the owner of RF workflow behavior.

## Responsibilities

The Python library owns:

- receiver/session lifecycle
- one-active-workflow enforcement
- decoder subprocess command construction
- scan and baseline state
- candidate ranking
- catalog/range/channel/favorite matching
- history persistence and exports
- listen frequency command previews

The FastAPI app owns:

- translating HTTP/WebSocket payloads into `SessionManager` calls
- serving static demo assets
- adapting model objects to JSON responses
- surfacing demo downloads for CSV/JSON inspection

The static browser UI owns:

- rendering tracks, spectrum, waterfall, events, candidates, and status
- collecting form values for explicit Python API calls
- short UI-only debounce behavior for noisy controls
- optimistic display updates that are reconciled by API/WebSocket responses

## Rules For Future UI Work

- Do not add scanner concepts that exist only in JavaScript.
- Do not store runtime RF state in browser storage as the source of truth.
- Do not hardcode frequency ranges/channels in static assets.
- Do not make the demo server required for normal Python library use.
- Prefer adding or tightening Python APIs before adding browser workflow logic.

## Current Demo Notes

The demo still contains presentation helpers for frequency labels and form defaults. Python remains the source of truth:

- catalog data comes from `FrequencyCatalog`
- favorites come from SQLite through Python endpoints
- live and persisted spectrum events are enriched by Python before rendering
- frequency discovery and baseline workflows are Python `SessionManager` calls

The eventual UI overhaul should focus on usability and layout without moving workflow decisions back into JavaScript.
