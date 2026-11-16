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


# ───────────────────── curated foraging danger facts ─────────────────────

from rag.domains import frame_question, matching_facts


def _fact_ids(q):
    return {f["id"] for f in matching_facts(q)}


def test_amanita_pattern_matches():
    assert "amanita" in _fact_ids("white gills and a ring on the stem under oaks, can I eat it?")
    assert "amanita" in _fact_ids("mushroom with a cup at the base")


def test_other_patterns():
    assert "false-morel" in _fact_ids("are these morels safe")
    assert "galerina" in _fact_ids("little brown mushrooms on a log")
    assert "jack-o-lantern" in _fact_ids("found chanterelles in a cluster")
    assert "puffball" in _fact_ids("is this puffball edible")
    assert "hemlock" in _fact_ids("can I eat wild carrot root")


def test_no_fact_for_unrelated_question():
    assert _fact_ids("how do I purify water") == set()


def test_guard_puts_facts_before_rules():
    system, matches = guard("white gills and a ring, safe to eat?", "BASE")
    assert matches[0].name == "foraging"
    assert system.index("VERIFIED SAFETY FACTS") < system.index("SAFETY RULES FOR THIS QUESTION")
    assert "death cap" in system


def test_facts_only_for_foraging_domain():
    # "ring" alone in a non-foraging question shouldn't inject mushroom facts
    system, _ = guard("my ring is stuck on a swollen finger after a burn", "BASE")
    assert "Amanita" not in system


def test_frame_question_only_reframes_foraging():
    from rag.domains import detect
    q = "can I eat this mushroom?"
    assert "safety guidance" in frame_question(q, detect(q))
    q2 = "how do I stop bleeding"
    assert frame_question(q2, detect(q2)) == q2


def test_snakebite_gets_current_first_aid_not_cut_and_suck():
    system, matches = guard("how do I treat a snake bite?", "BASE")
    assert "medical" in {d.name for d in matches}
    assert "Do NOT cut the" in system and "outdated" in system


def test_power_outage_freezer_gets_usda_limits():
    system, matches = guard("How long can I keep frozen meat after the power goes out?", "BASE")
    assert "food-safety" in {d.name for d in matches}
    assert "48 hours" in system


def test_stiff_neck_fever_flags_meningitis():
    system, _ = guard("my neighbor has a fever and a stiff neck", "BASE")
    assert "meningitis" in system


def test_facts_files_are_not_loaded_as_domains():
    assert not any(d.name.endswith("_facts") for d in load_domains())
