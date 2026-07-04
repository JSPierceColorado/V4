from ingest import parse_csv_bytes


def test_parse_csv_with_symbol_and_score() -> None:
    parsed = parse_csv_bytes(b"symbol,score,note\nMSFT,87,good\nAAPL,99,best\n")
    assert parsed["kind"] == "csv"
    assert parsed["symbol_column"] == "symbol"
    assert parsed["score_column"] == "score"
    assert parsed["symbols"][:2] == ["AAPL", "MSFT"]
    assert parsed["top_candidates"][0]["symbol"] == "AAPL"
