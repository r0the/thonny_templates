from thonnycontrib.thonny_templates.packages import (
    missing_distributions,
    normalize_distribution_name,
)


def test_normalize_distribution_name():
    assert normalize_distribution_name("Pygame_CE") == "pygame-ce"
    assert normalize_distribution_name("pgturbo") == "pgturbo"


def test_missing_distributions():
    assert missing_distributions(["pgturbo"], {"Pgturbo"}) == []
    assert missing_distributions(["pgturbo", "pgzero"], {"pgzero"}) == ["pgturbo"]
    assert missing_distributions([], {"pgzero"}) == []
