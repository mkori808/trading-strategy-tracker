from engine import alpaca_client, alpaca_trading


def test_paper_api_secret_alias_is_accepted(monkeypatch):
    names = (
        "ALPACA_API_KEY", "ALPACA_PAPER_API_KEY", "APCA_API_KEY_ID", "ALPACA_API_KEY_ID",
        "ALPACA_SECRET_KEY", "ALPACA_API_SECRET", "ALPACA_PAPER_SECRET_KEY",
        "ALPACA_PAPER_API_SECRET", "APCA_API_SECRET_KEY", "ALPACA_SECRET",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "paper-secret")

    assert alpaca_client._credentials() == ("paper-key", "paper-secret")
    assert alpaca_trading._credentials() == ("paper-key", "paper-secret")
