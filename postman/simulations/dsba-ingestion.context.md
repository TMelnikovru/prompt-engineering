# DSBA ingestion simulation

2026-10-25: response shapes were read from the public DSBA web client's
`/api/config`, `/api/subjects`, `/api/deadlines`, `/api/materials` calls.
Orbit returned no provider definition. Owner mock lookup was unavailable
because Postman CLI is signed out; the local mock uses observed shapes with
explicitly synthetic content, not a claimed owner contract.

Start from the repository root:

```sh
postman simulation run postman/simulations/dsba-ingestion.sim.yaml \
  --port 4912 --no-history --output ndjson
```

Use the URL from `listening` as `HSE_DSBA_BASE_URL`, including the `/dsba`
route prefix. Run the real application with a separate PostgreSQL database
ending in `_test`; apply `alembic upgrade head` first. No real credentials
or OpenAI calls are needed. The tests in `tests/test_dsba_integration.py`
exercise the same handler over real HTTP, with PostgreSQL and FastAPI.

The mock exposes POST `/__control` with `revision: 2` for a changed deadline
and `mode` values `ok`, `deadline-error`, `all-error`, `catalog-error`,
`malformed`, `invalid-date`, `slow`, `auth`, `conflict`, `deleted`, `no-materials`.
These control routes exist only in the stand-in.

The gateway run on 2026-10-25 exercised catalog and group-selected GETs,
FastAPI sync/dashboard, 60-second cached repeats, a changed deadline,
HTML 500 partial/full outages and recovery. PostgreSQL retained four
documents and five versions; no duplicate assignment was created.
`dsba-public` served the real connector's requests in the gateway request
log. It is the only member; no member remained unwired. No OpenAI member
is needed because DSBA deadlines are structured source fields.

A passing simulation proves local routing, parsing and persistence against
these observed shapes. Live requests separately verified the real public
catalog and records from both Mac and the production server network. It
does not establish access to private Classroom or PDF contents, nor a
permanent guarantee that the site's public interface will stay unchanged.

2026-10-25 combined release run: gateway port 4914, real FastAPI and isolated
PostgreSQL. Group 261 sync passed two Postman assertions; dashboard passed three
assertions for three tasks, one standalone material and material provenance.
Native Chrome completed local profile, catalog/group selection, Sources,
Materials (standalone lecture plus task link) and Tasks (exact/date-only/unknown
deadlines). All upstream content was synthetic; the dsba member served the
connector calls in NDJSON. No live production profile was selected by the test.
