from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.developer_verifier import (
    DeveloperVerifierService,
    extract_tax_ids,
    validate_nip_checksum,
)


def test_validate_nip_checksum():
    # Valid Polish NIPs
    assert validate_nip_checksum("5252248481") is True  # Ministry of Finance sample
    assert validate_nip_checksum("7740001454") is True  # Orlen
    assert validate_nip_checksum("5220003782") is True  # Asseco Poland
    # Invalid NIPs
    assert validate_nip_checksum("1234567890") is False
    assert validate_nip_checksum("5252248480") is False
    assert validate_nip_checksum("abc") is False


def test_extract_tax_ids():
    text = (
        "Inwestycja Osiedle Zielone budowana przez ABC Development Sp. z o.o.\n"
        "NIP: 525-224-84-81, KRS: 0000123456, REGON: 123456789."
    )
    ids = extract_tax_ids(text)
    assert ids["nip"] == "5252248481"
    assert ids["krs"] == "0000123456"
    assert ids["regon"] == "123456789"


def test_extract_tax_ids_polish_format_and_pl_prefix():
    # Standard Polish 3-2-2-3 NIP format
    text1 = "Dane inwestora: NIP 525-22-48-481, kontakt@invest.pl"
    ids1 = extract_tax_ids(text1)
    assert ids1["nip"] == "5252248481"

    # PL-prefixed 10-digit NIP
    text2 = "Faktura: PL5252248481, biuro sprzedaży"
    ids2 = extract_tax_ids(text2)
    assert ids2["nip"] == "5252248481"


def test_extract_tax_ids_short_krs():
    text = "Kontakt do biura dewelopera: KRS: 45678, NIP 774-000-14-54"
    ids = extract_tax_ids(text)
    assert ids["nip"] == "7740001454"
    assert ids["krs"] == "0000045678"


def test_extract_company_name():
    from src.services.developer_verifier import extract_company_name

    text1 = "Inwestycję realizuje Nowoczesne Osiedle Sp. z o.o. w Krakowie."
    assert extract_company_name(text1) == "Nowoczesne Osiedle Sp. z o.o."

    text2 = "Biuro sprzedaży: Dom Dla Każdego\nZadzwoń do nas!"
    assert extract_company_name(text2) == "Dom Dla Każdego"


@pytest.mark.asyncio
async def test_audit_developer_private_seller():
    svc = DeveloperVerifierService()
    res = await svc.audit_developer(description="Sprzedam bezpośrednio dom.", is_private_owner=True)
    assert res["developer_risk_level"] == "PRIVATE"
    assert any("prywatne" in r for r in res["developer_risk_reasons"])


def test_evaluate_risk_high_liquidation():
    svc = DeveloperVerifierService()
    krs_data = {
        "krs": "0000123456",
        "name": "Upadłość Deweloperka Sp. z o.o.",
        "capital_pln": 5000.0,
        "registration_year": 2023,
        "is_in_liquidation": True,
        "is_in_bankruptcy": False,
        "is_in_restructuring": False,
        "has_arrears_or_enforcement": False,
    }
    level, reasons = svc.evaluate_risk(None, krs_data)
    assert level == "HIGH"
    assert any("LIKWIDACJI" in r for r in reasons)


def test_evaluate_risk_low_established():
    svc = DeveloperVerifierService()
    vat_data = {"status_vat": "CZYNNY"}
    krs_data = {
        "krs": "0000123456",
        "name": "Duży Deweloper S.A.",
        "capital_pln": 2_000_000.0,
        "registration_year": 2012,
        "is_in_liquidation": False,
        "is_in_bankruptcy": False,
        "is_in_restructuring": False,
        "has_arrears_or_enforcement": False,
    }
    level, reasons = svc.evaluate_risk(vat_data, krs_data)
    assert level == "LOW"
    assert any("Wysoki kapitał" in r for r in reasons)
    assert any("Doświadczony podmiot" in r for r in reasons)


@pytest.mark.asyncio
async def test_audit_developer_mocked():
    svc = DeveloperVerifierService()
    client = AsyncMock()

    # Mock Biała lista response
    mock_vat_resp = MagicMock()
    mock_vat_resp.status_code = 200
    mock_vat_resp.json.return_value = {
        "result": {
            "subject": {
                "name": "Solidny Dom Sp. z o.o.",
                "nip": "5252248481",
                "statusVat": "Czynny",
                "krs": "0000555555",
            }
        }
    }

    # Mock KRS response
    mock_krs_resp = MagicMock()
    mock_krs_resp.status_code = 200
    mock_krs_resp.json.return_value = {
        "odpis": {
            "naglowekA": {"dataRejestracjiWKRS": "2015-05-10"},
            "dane": {
                "dzial1": {
                    "danePodmiotu": {
                        "formaPrawna": "SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
                        "nazwa": "Solidny Dom Sp. z o.o.",
                    },
                    "kapital": {"wysokoscKapitaluZakladowego": {"wartosc": "100 000,00"}},
                },
                "dzial4": {},
                "dzial6": {},
            },
        }
    }

    client.get.side_effect = [mock_vat_resp, mock_krs_resp]

    desc = "Sprzedaż bezpośrednia od dewelopera. NIP: 525-224-84-81. Zapraszamy!"
    res = await svc.audit_developer(client, desc)

    assert res["developer_name"] == "Solidny Dom Sp. z o.o."
    assert res["developer_nip"] == "5252248481"
    assert res["developer_krs"] == "0000555555"
    assert res["developer_capital_pln"] == 100000.0
    assert res["developer_registration_year"] == 2015
    assert res["developer_risk_level"] == "LOW"
