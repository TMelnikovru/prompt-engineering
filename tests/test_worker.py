from app.config import Settings
from app.ingest import retry_delay


def test_retry_backoff_is_capped_and_success_resets_normal_interval():
    settings = Settings(_env_file=None, wiki_retry_seconds=60, wiki_interval_seconds=900)
    assert [retry_delay(settings, n) for n in range(7)] == [900, 60, 120, 240, 480, 900, 900]
    assert retry_delay(settings, 10000) == 900
    assert retry_delay(settings, 0) == 900
