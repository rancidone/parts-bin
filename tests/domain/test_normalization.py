from domain.normalization import normalize_value


class TestNormalizeResistance:
    def test_bare_r(self):
        assert normalize_value("10R", "resistor") == "10r"

    def test_ohm_suffix(self):
        assert normalize_value("10ohm", "resistor") == "10r"

    def test_k(self):
        assert normalize_value("10K", "resistor") == "10k"

    def test_kohm(self):
        assert normalize_value("10kohm", "resistor") == "10k"

    def test_mega(self):
        assert normalize_value("1M", "resistor") == "1m"

    def test_eia_2r2(self):
        assert normalize_value("2R2", "resistor") == "2.2r"

    def test_eia_5k1(self):
        assert normalize_value("5K1", "resistor") == "5.1k"

    def test_eia_1m5(self):
        assert normalize_value("1M5", "resistor") == "1.5m"

    def test_decimal(self):
        assert normalize_value("2.2", "resistor") == "2.2r"

    def test_case_insensitive(self):
        assert normalize_value("10k", "resistor") == normalize_value("10K", "resistor")


class TestNormalizeCapacitance:
    def test_nf(self):
        assert normalize_value("100nF", "capacitor") == "100n"

    def test_uf_to_u(self):
        assert normalize_value("0.1uF", "capacitor") == "0.1u"

    def test_pf(self):
        assert normalize_value("100pF", "capacitor") == "100p"

    def test_eia_2n2(self):
        assert normalize_value("2n2", "capacitor") == "2.2n"

    def test_eia_4u7(self):
        assert normalize_value("4u7", "capacitor") == "4.7u"


class TestNormalizeInductance:
    def test_uh(self):
        assert normalize_value("10uH", "inductor") == "10u"

    def test_mh(self):
        assert normalize_value("0.01mH", "inductor") == "0.01m"

    def test_eia_4u7(self):
        assert normalize_value("4u7", "inductor") == "4.7u"


class TestNormalizeUnknownCategory:
    def test_passthrough_lowercased(self):
        assert normalize_value("ABC123", "transistor") == "abc123"




class TestUnsupportedValues:
    def test_incompatible_suffix_does_not_invent_resistance(self):
        assert normalize_value('1uF', 'resistor') == '1uf'
        assert normalize_value('10F', 'resistor') == '10f'
        assert normalize_value('10xyz', 'resistor') == '10xyz'

    def test_unknown_capacitance_and_inductance_units_remain_unknown(self):
        assert normalize_value('100', 'capacitor') == '100'
        assert normalize_value('10xyz', 'inductor') == '10xyz'
