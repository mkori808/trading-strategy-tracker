from engine.sharadar_pit_adapter import normalize_security, publishable


def test_adapter_derives_identity_and_delisting_reason_but_fails_closed() -> None:
    row = normalize_security(
        {
            "ticker": "OLD",
            "permaticker": "123",
            "firstpricedate": "2000-01-03",
            "lastpricedate": "2020-06-30",
            "exchange": "NYSE",
            "category": "Common Stock",
        },
        [{"date": "2020-06-30", "action": "delist", "value": "bankruptcy"}],
    )
    assert row.security_id == "123"
    assert row.effective_start == "2000-01-03"
    assert row.delisting_reason == "bankruptcy"
    assert row.known_at is None
    assert "known_at" in row.unresolved
    assert "DelistingReturn" in row.unresolved
    assert not publishable(row)


def test_adapter_never_promotes_missing_permanent_id_or_action_semantics() -> None:
    row = normalize_security({"ticker": "REUSED", "exchange": "NASDAQ"}, [])
    assert row.security_id == "ticker:REUSED"
    assert "security_id" in row.unresolved
    assert "known_at" in row.unresolved
    assert "delisting_status" in row.unresolved
    assert not publishable(row)

