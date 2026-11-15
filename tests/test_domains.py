from rag.domains import detect, guard, load_domains


def _names(q):
    return {d.name for d in detect(q)}


def test_domains_load():
    names = {d.name for d in load_domains()}
    assert {"foraging", "medical", "electrical"} <= names


def test_foraging_detected():
    assert "foraging" in _names("Can I eat this mushroom from my yard?")
    assert "foraging" in _names("which wild edibles grow in Ohio")


def test_medical_detected():
    assert "medical" in _names("how do I stop heavy bleeding")


def test_electrical_detected():
    assert "electrical" in _names("can I backfeed my house with a generator")


def test_no_false_positive_on_plain_question():
    assert _names("what is the capital of Spain") == set()


def test_word_boundary_avoids_substring_match():
    # "burn" should not fire on "Hepburn"
    assert "medical" not in _names("tell me about Audrey Hepburn")


def test_guard_appends_tail():
    system, matches = guard("is this mushroom safe to eat", "BASE")
    assert system.startswith("BASE")
    assert "look-alikes" in system
    assert matches and matches[0].name == "foraging"


def test_guard_passthrough_when_no_match():
    system, matches = guard("history of Rome", "BASE")
    assert system == "BASE"
    assert matches == []
