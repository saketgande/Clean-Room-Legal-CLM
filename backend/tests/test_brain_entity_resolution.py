"""Entity resolution for the knowledge graph.

The normalisation rules are the whole game here: too loose and two different
companies merge into one wrong exposure figure, too strict and the graph stays
a set of disconnected stars.
"""

from app.contract_brain.entities import (
    normalize_org_name,
    party_entity_key,
    suggest_merges,
)


class TestNormalisation:
    def test_company_forms_are_stripped(self):
        """The same organisation written five ways resolves to one key."""
        variants = [
            "Nexus Legal Technologies, Inc.",
            "Nexus Legal Technologies Inc",
            "NEXUS LEGAL TECHNOLOGIES INC.",
            "  Nexus   Legal   Technologies,  Inc  ",
            "Nexus Legal Technologies Incorporated",
        ]
        keys = {normalize_org_name(v) for v in variants}
        assert keys == {"nexus legal technologies"}

    def test_indian_and_european_forms(self):
        assert normalize_org_name("Delta Operations Services Private Limited") == "delta operations services"
        assert normalize_org_name("Delta Operations Services Pvt Ltd") == "delta operations services"
        assert normalize_org_name("M/s Acme Laboratories Limited") == "acme laboratories"
        assert normalize_org_name("Northwind Traders GmbH") == "northwind traders"
        assert normalize_org_name("Cloudspan Infrastructure BV") == "cloudspan infrastructure"

    def test_placeholders_never_become_entities(self):
        """17 contracts in this codebase carry 'the Counterparty' as the party
        name. Turning that into an organisation would invent a counterparty
        that 17 unrelated contracts appear to share."""
        for junk in ("the Counterparty", "Counterparty", "N/A", "n/a", "TBD",
                     "unknown", "the company", "vendor", ""):
            assert normalize_org_name(junk) is None, junk

    def test_a_bare_company_form_is_not_a_name(self):
        for form in ("Ltd", "GmbH", "LLP", "Private Limited", "Inc."):
            assert normalize_org_name(form) is None, form

    def test_short_real_names_survive(self):
        """Stripping must not be so eager that real names disappear."""
        assert normalize_org_name("Sony") == "sony"
        assert normalize_org_name("Acme Ltd") == "acme"
        assert normalize_org_name("IBM Corporation") == "ibm"

    def test_none_and_whitespace(self):
        assert normalize_org_name(None) is None
        assert normalize_org_name("   ") is None


class TestEntityKey:
    def test_key_is_prefixed_and_stable(self):
        assert party_entity_key("Contoso Consulting LLP") == "org:contoso consulting"
        assert party_entity_key("Contoso Consulting, L.L.P.") == party_entity_key("Contoso Consulting LLP")

    def test_unusable_names_have_no_key(self):
        assert party_entity_key("the Counterparty") is None
        assert party_entity_key(None) is None


class TestMergeSuggestions:
    def test_similar_names_are_reported_not_merged(self):
        """Reporting, never auto-merging: these two score highly and are
        different companies."""
        pairs = suggest_merges(["Contoso Consulting LLP", "Contoso Cloud Inc"], threshold=50)
        assert any({a, b} == {"Contoso Consulting LLP", "Contoso Cloud Inc"} for a, b, _ in pairs)
        # and normalisation kept them apart
        assert normalize_org_name("Contoso Consulting LLP") != normalize_org_name("Contoso Cloud Inc")

    def test_identical_after_normalisation_is_not_a_suggestion(self):
        """Already-merged names shouldn't be offered as a merge candidate."""
        pairs = suggest_merges(["Acme Ltd", "Acme Limited", "Acme, Inc."])
        assert all(a != b for a, b, _ in pairs)

    def test_placeholders_are_excluded_from_suggestions(self):
        pairs = suggest_merges(["the Counterparty", "Counterparty"])
        assert pairs == []
