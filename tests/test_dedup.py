from agent.db.dedup import claim_update_id


def test_first_claim_wins(aws):
    assert claim_update_id(123) is True


def test_duplicate_claim_returns_false(aws):
    assert claim_update_id(456) is True
    assert claim_update_id(456) is False


def test_different_ids_are_independent(aws):
    assert claim_update_id(1) is True
    assert claim_update_id(2) is True
    assert claim_update_id(1) is False
