from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.gunb import GunbService


def test_generate_gunb_url():
    svc = GunbService()
    assert svc.generate_gunb_url() == "https://wyszukiwarka.gunb.gov.pl/"
    url = svc.generate_gunb_url("181609_2.0001.2643/7")
    assert url == "https://wyszukiwarka.gunb.gov.pl/?dzialka=181609_2.0001.2643/7"


def test_parse_rwdz_payload_xml():
    svc = GunbService()
    sample_xml = """
    <GetFeatureInfo_Result>
        <ROWSET name="Decyzje">
            <ROW>
                <NR_DECYZJI>AB.6740.1.2024</NR_DECYZJI>
                <NAZWA_ZAMIERZENIA>Budowa budynku mieszkalnego jednorodzinnego</NAZWA_ZAMIERZENIA>
                <RODZAJ_OBIEKTU>Budynek mieszkalny</RODZAJ_OBIEKTU>
                <DATA_DECYZJI>2024-03-15</DATA_DECYZJI>
                <STATUS>DECYZJA POZYTYWNA</STATUS>
            </ROW>
        </ROWSET>
    </GetFeatureInfo_Result>
    """
    permits = svc.parse_rwdz_payload(sample_xml)
    assert len(permits) == 1
    p = permits[0]
    assert p["numer_decyzji"] == "AB.6740.1.2024"
    assert "jednorodzinnego" in p["nazwa_zamierzenia"]
    assert p["rodzaj_obiektu"] == "Budynek mieszkalny"
    assert p["data_decyzji"] == "2024-03-15"
    assert p["status"] == "DECYZJA POZYTYWNA"


def test_parse_rwdz_payload_geojson():
    svc = GunbService()
    sample_json = """{
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "nr_decyzji": "WNZ.123/2023",
                    "nazwa_zamierzenia": "Budowa hali magazynowej z zapleczem socjalnym",
                    "rodzaj_obiektu": "Magazyn",
                    "data_decyzji": "2023-11-20",
                    "status": "WYDANA"
                }
            }
        ]
    }"""
    permits = svc.parse_rwdz_payload(sample_json)
    assert len(permits) == 1
    assert permits[0]["numer_decyzji"] == "WNZ.123/2023"
    assert "hali magazynowej" in permits[0]["nazwa_zamierzenia"]


def test_evaluate_neighborhood_risks():
    svc = GunbService()
    permits = [
        {
            "numer_decyzji": "10/2024",
            "nazwa_zamierzenia": "Budynek mieszkalny jednorodzinny",
            "rodzaj_obiektu": "Mieszkalny",
        },
        {
            "numer_decyzji": "88/2024",
            "nazwa_zamierzenia": "Budowa hali magazynowej wysokiego składowania",
            "rodzaj_obiektu": "Przemysłowy",
            "data_decyzji": "2024-06-01",
        },
        {
            "numer_decyzji": "99/2024",
            "nazwa_zamierzenia": "Budowa stacji bazowej telefonii komórkowej z wieżą 45m",
            "rodzaj_obiektu": "Telekomunikacyjny",
        },
    ]

    flags = svc.evaluate_neighborhood_risks(permits)
    assert len(flags) == 2
    assert any("Hala magazynowa" in f for f in flags)
    assert any("stacji bazowej" in f.lower() or "wież" in f.lower() for f in flags)
    # The first permit is standard residential so should not generate a risk flag
    assert not any("jednorodzinny" in f and "Hala" not in f for f in flags)


@pytest.mark.asyncio
async def test_audit_gunb_permits_mocked():
    svc = GunbService()
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = """
    <ROWSET>
        <ROW>
            <NR_DECYZJI>DEC-456</NR_DECYZJI>
            <NAZWA_ZAMIERZENIA>Budowa fermy drobiu na 50 000 sztuk</NAZWA_ZAMIERZENIA>
            <RODZAJ_OBIEKTU>Rolniczy/przemysłowy</RODZAJ_OBIEKTU>
            <DATA_DECYZJI>2024-01-10</DATA_DECYZJI>
        </ROW>
    </ROWSET>
    """
    mock_client.get.return_value = mock_resp

    res = await svc.audit_gunb_permits(
        mock_client,
        cx=200000.0,
        cy=700000.0,
        parcel_id="181609_2.0001.2643/7",
    )

    assert len(res["gunb_permits"]) == 1
    assert len(res["gunb_risk_flags"]) == 1
    assert "ferma" in res["gunb_risk_flags"][0].lower() or "drobiu" in res["gunb_risk_flags"][0].lower()
    assert res["gunb_status"] == "RYZYKO_W_SĄSIEDZTWIE"
    assert "dzialka=181609_2.0001.2643/7" in res["gunb_url"]
