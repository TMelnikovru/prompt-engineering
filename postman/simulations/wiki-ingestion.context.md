# Wiki ingestion simulation

Contract snapshots: MediaWiki Action API formatversion=2 (live schema checked
2026-10-25) and official OpenAI Responses Structured Outputs documentation.
Handlers contain synthetic course content. No owner's workspace was supplied;
these are local owned mocks, not vendor-published mocks.

## Purpose

Runs the actual WikiConnector and structured_response HTTP clients against
stand-ins. Recovery was also run through worker_cycle with real PostgreSQL.
Handlers and control routes are in postman/mocks.

## Execution

Start: `postman simulation run postman/simulations/wiki-ingestion.sim.yaml --port 4910 --output ndjson`.
Read the listening event for the bound port. Set HSE_WIKI_API_URL to
`http://127.0.0.1:4910/wiki/api.php`, HSE_OPENAI_BASE_URL to
`http://127.0.0.1:4910/openai/v1`, and OPENAI_API_KEY to a dummy value.
Never use a real API key for this simulation.
The pytest fixtures run the same handlers directly with Node on ephemeral ports.

## Assumptions

- Response shapes reflect the fields this client consumes; model quality,
  real account limits and site availability require live checks.
  (observed 2026-10-25, client smoke run and official wiki probe)
- Control endpoints are test-only, not vendor API operations.
  (reasoned 2026-10-25, mock handlers)

## Observations

<!-- begin:observations -->
2026-10-25: both fkn-wiki and openai-responses served real client calls;
the parse result and typed ExtractedChunk completed. No member stayed silent.
2026-10-25 recovery run: worker_cycle used an isolated PostgreSQL test database
and the listening gateway on 4910. POST /wiki/__control mode=slow with client
timeout=0.1 yielded wiki_timeout; then mode=ok plus /openai/__control
mode=rate-limit persisted the page and left extraction pending after HTTP 429.
Switching OpenAI to ok completed extraction and summary; a repeat reused the
summary with zero tokens. Both members served requests in the NDJSON log.
The same recovery and worker restart are automated in
tests/test_ingestion_integration.py against the same HTTP handlers.
This proves recovery against the stand-ins, not availability of the real wiki.
<!-- end:observations -->

2026-10-25 Telegram digest run: sync_telegram persisted two synthetic messages
in an isolated PostgreSQL database using an MTProto transport double, then
summarize_telegram called the running gateway at /openai/v1/responses.
The NDJSON log recorded one POST, status 200; repeating the digest used the
database cache without another model call. openai-responses served requests;
fkn-wiki stayed silent because this path reads Telegram, not wiki.
This does not establish real Telegram authorization or model output quality.
