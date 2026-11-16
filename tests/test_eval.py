from rag.eval import check, load_cases


def test_cases_file_is_well_formed():
    cases = load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        assert c["question"].strip()
        assert c.get("must") or c.get("must_not"), c["id"]


def test_must_groups_accept_any_alternative():
    case = {"must": [["boil", "boiling"], ["minute"]]}
    assert check(case, "Bring it to a rolling BOIL for one minute.") == []
    assert check(case, "Use iodine tablets.") == ["missing one of: boil, boiling", "missing one of: minute"]


def test_must_not_catches_dangerous_advice():
    case = {"must_not": ["suck out the venom"]}
    assert check(case, "Cut an X and suck out the venom.") == ["contains forbidden: suck out the venom"]
    assert check(case, "Keep still and get to a hospital.") == []


def test_negated_forbidden_phrase_is_fine():
    case = {"must_not": ["suck out the venom", "apply a tourniquet"]}
    assert check(case, "Do not try to suck out the venom. Never apply a tourniquet.") == []
    assert check(case, "Don't panic. Then suck out the venom.") == ["contains forbidden: suck out the venom"]


def test_max_chars():
    assert check({"max_chars": 10}, "x" * 11) == ["too long: 11 > 10 chars"]
