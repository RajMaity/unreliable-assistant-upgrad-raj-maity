from ingress.triage import Route, triage


def test_routes() -> None:
    assert triage("What is the storage condition for LOT-00044?") is Route.RETRIEVAL
    assert triage("Release LOT-00044 from hold") is Route.TRANSACTIONAL
    assert triage("Release the hold") is Route.RETRIEVAL  # no entity, so not a Tier-3 target
    assert triage("   ") is Route.EMPTY
