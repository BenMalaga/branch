"""Every figure the assistant states must trace back to a tool that actually ran.

This is the check that does not need an API key and cannot itself hallucinate:
plain arithmetic against the recorded tool output. It has to be right in both
directions. Flagging honest rounding would train people to ignore the warning,
and missing an invented number defeats the point.
"""
from branch import receipts

STEPS = [
    {"tool": "clip", "recipe": {"tool": "clip", "kept": 237, "of": 337},
     "result": {"type": "FeatureCollection", "features": [{} for _ in range(237)]}},
    {"tool": "cost_estimate", "recipe": {"tool": "cost_estimate", "quantity": 20},
     "result": {"estimated_total_usd": 8000, "unit_cost_usd": 400}},
]
QUESTION = "how much for 20 trees, and how many restaurants are in the bronx"


def test_an_answer_built_only_from_tool_output_is_clean():
    answer = ("237 restaurants are inside the border, out of 337. Twenty trees "
              "would cost $8,000, at $400 each.")
    r = receipts.verify(answer, STEPS, QUESTION)
    assert r["clean"] and r["traced"] == r["checked"] and r["checked"] >= 4


def test_an_invented_figure_is_caught():
    answer = ("237 restaurants are inside the border. Median household income "
              "there is $41,895.")
    r = receipts.verify(answer, STEPS, QUESTION)
    assert not r["clean"]
    assert any("41,895" in o["value"] for o in r["orphans"])
    assert r["orphans"][0]["phrase"]          # the reader is shown where to look


def test_rounding_is_not_treated_as_invention():
    """"About 240" for 237 and "$8k" for 8000 are honest prose, not errors."""
    r = receipts.verify("About 240 restaurants, and roughly $8k of trees.",
                        STEPS, QUESTION)
    assert r["clean"], r["orphans"]


def test_numbers_the_user_supplied_are_legitimate():
    r = receipts.verify("You asked about 20 trees, and that costs $8,000.",
                        STEPS, QUESTION)
    assert r["clean"], r["orphans"]


def test_years_and_step_numbers_are_not_measurements():
    r = receipts.verify("In step 2 I used the 2015 census to find 237 matches.",
                        STEPS, QUESTION)
    assert r["clean"], r["orphans"]


def test_a_feature_count_counts_as_a_source():
    """"237 features" is traceable even though no recipe field says 237."""
    steps = [{"tool": "osm", "recipe": {"tool": "osm"},
              "result": {"type": "FeatureCollection",
                         "features": [{} for _ in range(412)]}}]
    r = receipts.verify("I found 412 of them.", steps, "")
    assert r["clean"]


def test_suffixes_are_understood():
    assert receipts.parse_number("$1.2M") == 1200000
    assert receipts.parse_number("3.5k") == 3500
    assert receipts.parse_number("1,234,567") == 1234567
    assert receipts.parse_number("12.5%") == 12.5


def test_an_answer_with_no_numbers_makes_no_claim():
    r = receipts.verify("I could not find a border for that place.", STEPS, "")
    assert r["checked"] == 0 and not r["clean"]      # nothing to vouch for
