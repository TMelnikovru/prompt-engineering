import asyncio

import httpx
import pytest

from app.config import Settings
from app.connectors.dsba import DsbaConnector, DsbaError, DsbaSelection, deadline, public_url


def test_date_precision_and_timezone_are_not_invented():
    assert deadline("2026-11-08", 1) == {"due_at": None, "due_date": "2026-11-08"}
    assert deadline(None, 0) == {"due_at": None, "due_date": None}
    assert deadline("2026-10-28T20:59:00.000Z", 0)["due_at"] == "2026-10-28T20:59:00+00:00"
    with pytest.raises(DsbaError, match="timezone_unknown"):
        deadline("2026-10-28T23:59:00", 0)
    with pytest.raises(DsbaError, match="invalid_deadline"):
        deadline("2026-02-30", 1)


def test_scope_hash_is_stable_and_separates_groups():
    first = DsbaSelection(program="dsba", year=1, group="261", subjects=["cpp", "calculus"])
    same = DsbaSelection(program="dsba", year=1, group="261", subjects=["calculus", "cpp", "cpp"])
    other = DsbaSelection(program="dsba", year=1, group="262", subjects=["cpp", "calculus"])
    assert first.source_id == same.source_id
    assert first.source_id != other.source_id


def test_untrusted_links_do_not_add_credentials_or_executable_schemes():
    assert public_url("javascript:alert(1)", "https://dsba.online") is None
    assert public_url("https://:password@example.test/file", "https://dsba.online") is None
    assert public_url("https://[invalid/file", "https://dsba.online") is None
    assert public_url("/files/53", "https://dsba.online") == "https://dsba.online/files/53"


def test_real_http_collects_selection_and_keeps_canonical_identifiers(upstreams):
    async def run():
        settings = Settings(_env_file=None, dsba_base_url=upstreams["dsba"])
        selection = DsbaSelection(program="dsba", year=1, group="261", sub="2")
        async with DsbaConnector(settings) as connector:
            catalog = await connector.catalog()
            connector.validate(selection, catalog)
            with pytest.raises(DsbaError, match="invalid_selection"):
                connector.validate(DsbaSelection(program="dsba", year=1, group="bogus"), catalog)
            result = await connector.collect(selection)
            assert not result.errors
            assert result.complete_kinds == {"deadline", "material"}
            assert len(result.records) == 4
            assert {r.source_id for r in result.records} == {selection.source_id}
            assert result.records[0].external_id == "deadline:31"
            assert "Срок в DSBA: 2026-10-28T20:59:00.000Z" in result.records[0].text
            assert "all_day" not in result.records[0].text
            assert "all_day" in result.records[0].raw_content
            assert result.records[-1].published_at is not None
            async with httpx.AsyncClient() as client:
                calls = (await client.get(upstreams["dsba"] + "/__stats")).json()["requests"]
            assert all(
                "group=261" in path and "sub=2" in path for path in calls if path != "/api/config"
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,code",
    [
        ("deadline-error", "dsba_http_500"),
        ("malformed", "dsba_invalid_response"),
        ("invalid-date", "dsba_invalid_deadline"),
    ],
)
def test_one_failed_endpoint_preserves_other_records_and_is_not_a_complete_snapshot(
    upstreams, mode, code
):
    async def run():
        async with httpx.AsyncClient() as client:
            await client.post(upstreams["dsba"] + "/__control", json={"mode": mode})
        settings = Settings(_env_file=None, dsba_base_url=upstreams["dsba"])
        async with DsbaConnector(settings) as connector:
            result = await connector.collect(DsbaSelection(program="dsba", year=1, group="261"))
        assert result.errors[0]["code"] == code
        assert "deadline" not in result.complete_kinds
        assert "material" in result.complete_kinds
        assert any(r.external_id.startswith("material:") for r in result.records)

    asyncio.run(run())


def test_timeout_and_authentication_do_not_become_successful_syncs(upstreams):
    async def run():
        async with httpx.AsyncClient() as client:
            await client.post(upstreams["dsba"] + "/__control", json={"mode": "slow"})
        settings = Settings(
            _env_file=None, dsba_base_url=upstreams["dsba"], dsba_request_timeout=0.05
        )
        async with DsbaConnector(settings) as connector:
            with pytest.raises(DsbaError, match="dsba_timeout"):
                await connector.catalog()
        async with httpx.AsyncClient() as client:
            await client.post(upstreams["dsba"] + "/__control", json={"mode": "auth"})
        async with DsbaConnector(settings) as connector:
            with pytest.raises(DsbaError, match="dsba_http_401"):
                await connector.catalog()

    asyncio.run(run())


def test_limits_do_not_claim_full_snapshot_and_conflicts_do_not_choose_a_deadline(upstreams):
    async def run():
        async with httpx.AsyncClient() as client:
            await client.post(upstreams["dsba"] + "/__control", json={"mode": "conflict"})
        settings = Settings(_env_file=None, dsba_base_url=upstreams["dsba"], dsba_max_records=1)
        async with DsbaConnector(settings) as connector:
            result = await connector.collect(DsbaSelection(program="dsba", year=1, group="261"))
        assert result.has_more
        assert "deadline" not in result.complete_kinds
        assert result.records[0].attributes["facts"]["conflict"]
        assert result.records[0].attributes["facts"]["due_at"] is None

    asyncio.run(run())
