# Wiki ingestion simulation

Contract snapshots: MediaWiki Action API formatversion=2 (live schema checked
2026-10-25) and official OpenAI Responses Structured Outputs documentation.
Handlers contain synthetic course content. No owner's workspace was supplied;
these are local owned mocks, not vendor-published mocks.

## Purpose

Runs the actual WikiConnector and structured_response HTTP clients against
stand-ins; database persistence is tested separately with real PostgreSQL in CI.
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
<!-- end:observations -->
