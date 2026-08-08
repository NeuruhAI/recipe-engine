from scripts import rank_nc_curative_raw_v2 as v2


def test_false_heir_institutional_names_are_rejected():
    assert v2.strict_heir("A & E REAL ESTATES LLC", True) is False
    assert v2.strict_heir("HRS HOLDING CO", True) is False
    assert v2.strict_heir("GF REAL ESTATE HOLDINGS LLC", True) is False


def test_real_heir_names_survive():
    assert v2.strict_heir("UNKNOWN HEIRS OF FREDDIE SOWELL", False) is True
    assert v2.strict_heir("PARKER LARRY HRS", False) is True
    assert v2.strict_heir("ESTATE OF JOHN DOE", False) is True
