import io
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from src.services.geoportal import GeoportalService


def _png_with_colored_pixels(colors: list[tuple[int, int, int]], size: int = 200) -> bytes:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    assert px is not None
    for i, color in enumerate(colors):
        for x in range(i * 20, i * 20 + 20):
            for y in range(10, 30):
                px[x, y] = (*color, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_geoportal_url_generator():
    svc = GeoportalService()
    url_parcel = svc.generate_geoportal_url(parcel_id="181609_2.0001.2643/7")
    assert "identifyParcel=181609_2.0001.2643/7" in url_parcel

    url_coords = svc.generate_geoportal_url(lat=50.04, lon=22.01)
    assert "locatePoint" in url_coords
    assert "50.04" in url_coords

    gunb_url = svc.generate_gunb_url()
    assert gunb_url == "https://wyszukiwarka.gunb.gov.pl/"


@pytest.mark.asyncio
async def test_get_mpzp_info_active():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<GetFeatureInfo_Result><ROWSET name='MPZP_PRZEZNACZENIE_TERENU'>"
        "<ROW><FUN_SYMB>MN</FUN_SYMB><FUN_NAZWA>tereny mieszkaniowe</FUN_NAZWA>"
        "<NAZWA_PLAN>Plan Centrum</NAZWA_PLAN></ROW></ROWSET></GetFeatureInfo_Result>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 100.0, 200.0)
    assert res["status"] == "OBOWIĄZUJĄCY"
    assert res["symbol"] == "MN"
    assert "MN: tereny mieszkaniowe" in res["zone"]
    assert res["plan_name"] == "Plan Centrum"


@pytest.mark.asyncio
async def test_get_mpzp_info_missing():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "m. Rzeszów: brak wyniku dla wskazanego obszaru"
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 300.0, 400.0)
    assert res["status"] == "BRAK_PLANU_LUB_CYFRYZACJI"
    assert "Brak MPZP" in res["zone"]


@pytest.mark.asyncio
async def test_get_mpzp_info_rzeszow_html_tables():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<TABLE border=1 width=100%><TR><TH width=25%>Layer</TH>"
        "<TD>mpzp_przeznaczenie_d0bec3b4</TD></TR>"
        "<TR><TH>SYMBOL</TH><TD>ZC</TD></TR>"
        "<TR><TH>OPIS</TH><TD>Teren cmentarza komunalnego</TD></TR>"
        "<TR><TH>UCHWALA</TH><TD>XXVII/418/2008</TD></TR></TABLE>"
        "<TABLE border=1 width=100%><TR><TH>NAZWA</TH><TD>MIASTA RZESZOWA</TD></TR>"
        "<TR><TH>NAZWA2</TH><TD>MPZP przy Cmentarzu Komunalnym Wilkowyja</TD></TR></TABLE>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 100.0, 200.0)
    assert res["status"] == "OBOWIĄZUJĄCY"
    assert res["symbol"] == "ZC"
    assert "ZC: Teren cmentarza komunalnego" in res["zone"]
    assert res["plan_name"] == "MPZP przy Cmentarzu Komunalnym Wilkowyja"


@pytest.mark.asyncio
async def test_get_mpzp_info_raster_plan():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<table><tr class='row'><th class='cell blue'>Identyfikator</th>"
        "<td class='cell'>PL.ZIPPZP.2506_186301-MPZP_50_granica</td></tr>"
        "<tr class='row'><th class='cell blue'>Poziom informatyzacji</th>"
        "<td class='cell'>rastrowy</td></tr>"
        "<tr class='row'><th class='cell blue'>Nazwa planu</th>"
        "<td class='cell'>MPZP Nr 75/5/2004 przy al. Cieplińskiego w Rzeszowie</td></tr></table>"
        "<hr/>m. Rzeszów: brak wyniku dla wskazanego obszaru"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 100.0, 200.0)
    assert res["status"] == "OBOWIĄZUJĄCY"
    assert res["level"] == "rastrowy"
    assert "MPZP Nr 75/5/2004" in res["plan_name"]


@pytest.mark.asyncio
async def test_get_mpzp_info_esri_table():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<html xmlns:esri_wms='http://www.esri.com/wms'>"
        "<h5>FeatureInfoCollection - layer name: 'Przeznaczenia MPZP'</h5>"
        "<table><tbody><tr>"
        "<th>id_pg</th><th>Uchwalenie</th><th>Oznaczenie</th><th>Nazwa MPZP</th>"
        "<th>opis_oznac</th></tr>"
        "<tr><td>3499</td><td>XII/131/11</td><td>KP.1</td><td>STARE MIASTO</td>"
        "<td>Tereny placów miejskich</td></tr></tbody></table></html>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 100.0, 200.0)
    assert res["status"] == "OBOWIĄZUJĄCY"
    assert res["symbol"] == "KP.1"
    assert "KP.1: Tereny placów miejskich" in res["zone"]
    assert res["plan_name"] == "STARE MIASTO"


@pytest.mark.asyncio
async def test_get_mpzp_info_cp1250_decoding():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    raw = (
        "<table><tr class='row'><th class='cell blue'>Poziom informatyzacji</th>"
        "<td class='cell'>rastrowy</td></tr>"
        "<tr class='row'><th class='cell blue'>Nazwa planu</th>"
        "<td class='cell'>Plan dla terenów lasów i zalesień</td></tr>"
        "<tr class='row'><th class='cell blue'>Uchwała</th>"
        "<td class='cell'>III/22/2002</td></tr></table>"
    ).encode("cp1250")
    mock_resp.content = raw
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 100.0, 200.0)
    assert res["status"] == "OBOWIĄZUJĄCY"
    assert res["level"] == "rastrowy"
    assert "lasów i zalesień" in res["plan_name"]


@pytest.mark.asyncio
async def test_get_mpzp_info_unknown_rows_not_false_brak():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<GetFeatureInfo_Result><ROWSET name='MPZP_PRZEZNACZENIE_TERENU'>"
        "<ROW num='1'><NIEZNANE_POLE>dane</NIEZNANE_POLE></ROW></ROWSET></GetFeatureInfo_Result>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_mpzp_info(client_mock, 100.0, 200.0)
    assert res["status"] == "NIEZNANY"
    assert res["zone"] is None


@pytest.mark.asyncio
async def test_get_flood_risk_isok_hazard():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "features": [
            {
                "id": "NZ.RiskZone.1",
                "properties": {"lor_qualitativevalue": "wysokie ryzyko"},
            }
        ]
    }
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_flood_risk_isok(client_mock, 500.0, 600.0)
    assert res["flood_zone"] == "ZAGROŻENIE_POWODZIOWE"
    assert res["risk_level"] == "wysokie ryzyko"
    assert "strefie zagrożenia powodziowego" in res["description"]


@pytest.mark.asyncio
async def test_get_flood_risk_isok_safe():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"features": []}
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_flood_risk_isok(client_mock, 700.0, 800.0)
    assert res["flood_zone"] == "BRAK"
    assert res["risk_level"] is None
    assert "poza strefą ISOK" in res["description"]


@pytest.mark.asyncio
async def test_get_gesut_networks_detects_networks():
    svc = GeoportalService()
    png = _png_with_colored_pixels([(0, 0, 255), (255, 0, 0), (128, 51, 0)])
    empty = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    empty_buf = io.BytesIO()
    empty.save(empty_buf, format="PNG")

    def _resp_for(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = png if "erzeszow" in url else empty_buf.getvalue()
        return resp

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.side_effect = _resp_for

    res = await svc.get_gesut_networks(client_mock, 50.0375, 22.0047)
    assert res["coverage"] is True
    assert res["networks"]["woda"] is True
    assert res["networks"]["prad"] is True
    assert res["networks"]["kanalizacja"] is True
    assert res["networks"]["gaz"] is False
    assert res["networks"]["cieplo"] is False
    assert res["networks"]["telekomunikacja"] is False
    assert res["sources"] == ["Miasto Rzeszów"]
    assert res["checked_radius_m"] > 0


@pytest.mark.asyncio
async def test_get_gesut_networks_no_coverage():
    svc = GeoportalService()
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = buf.getvalue()
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_gesut_networks(client_mock, 49.2, 22.35)
    assert res["coverage"] is False
    assert all(not v for v in res["networks"].values())
    assert res["sources"] == []


@pytest.mark.asyncio
async def test_get_landslide_risk_sopo_detected():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "features": [
            {
                "id": "sopo.1",
                "properties": {"NAZWA": "Osuwisko Dynów", "STAN": "aktywne"},
            }
        ]
    }
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_landslide_risk_sopo(client_mock, 715000.0, 215000.0)
    assert res["risk"] == "OSUWISKO"
    assert res["has_risk"] is True
    assert "Obszar osuwiska" in res["description"]


@pytest.mark.asyncio
async def test_get_landslide_risk_sopo_safe():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"features": []}
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_landslide_risk_sopo(client_mock, 716000.0, 216000.0)
    assert res["risk"] == "BRAK"
    assert res["has_risk"] is False
    assert "Brak osuwisk" in res["description"]


@pytest.mark.asyncio
async def test_get_egib_full_audit_building_and_soil():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<html><body><table>"
        "<tr><td>Oznaczenie konturu</td><td>B</td></tr>"
        "<tr><td>Klasoużytek</td><td>RIIIa</td></tr>"
        "</table></body></html>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_egib_full_audit(client_mock, 717000.0, 217000.0)
    assert res["building_status"] == "UJAWNIONY"
    assert res["soil_class"] == "RIIIa"
    assert res["is_protected_soil"] is True
    assert "RIIIa" in res["protected_classes"]


@pytest.mark.asyncio
async def test_get_egib_full_audit_missing_building():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<html><body><table>"
        "<tr><td>Oznaczenie konturu</td><td>R</td></tr>"
        "<tr><td>Klasa</td><td>RIVb</td></tr>"
        "</table></body></html>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_egib_full_audit(client_mock, 718000.0, 218000.0)
    assert res["building_status"] == "BRAK_W_EWIDENCJI"
    assert res["soil_class"] == "RIVb"
    assert res["is_protected_soil"] is False


@pytest.mark.asyncio
async def test_get_gdos_protected_areas():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "features": [
            {
                "id": "gdos.1",
                "properties": {"nazwa": "Dolina Wisłoka", "kodinspire": "PLH180030"},
            }
        ]
    }
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_gdos_protected_areas(client_mock, 719000.0, 219000.0)
    assert res["is_protected"] is True
    assert "Dolina Wisłoka" in res["zone_type"]
    assert "GDOŚ" in res["description"]


@pytest.mark.asyncio
async def test_get_nid_monuments():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = (
        "<TABLE class='mainTb'>"
        "<TR><TH>SITENAME</TH><TD>Dwór szlachecki</TD></TR>"
        "<TR><TH>LEGALFOUNDATIONDOCUMENT</TH><TD>Decyzja WKZ A-123/84</TD></TR>"
        "</TABLE>"
    )
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_nid_monuments(client_mock, 720000.0, 220000.0)
    assert res["is_monument"] is True
    assert res["name"] == "Dwór szlachecki"
    assert "A-123/84" in res["document"]


def test_get_cemetery_proximity():
    svc = GeoportalService()
    # 1. Direct cemetery in MPZP
    res_imm = svc.get_cemetery_proximity(100.0, 200.0, mpzp_zone="1.ZC: tereny cmentarza")
    assert res_imm["has_cemetery_risk"] is True
    assert res_imm["zone"] == "<50m"

    # 2. Cemetery in surrounding parcels
    res_surr = svc.get_cemetery_proximity(
        100.0, 200.0, mpzp_zone="MN", surrounding_risks=["Teren sąsiedni to cmentarz"]
    )
    assert res_surr["has_cemetery_risk"] is True
    assert res_surr["zone"] == "50-150m"

    # 3. Safe
    res_safe = svc.get_cemetery_proximity(100.0, 200.0, mpzp_zone="MN", surrounding_risks=[])
    assert res_safe["has_cemetery_risk"] is False
    assert res_safe["zone"] == "BRAK"


@pytest.mark.asyncio
async def test_get_noise_level_audit_transit_hub():
    svc = GeoportalService()
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.side_effect = Exception("API error")

    # Coords close to Węzeł Świlcza (50.0631, 21.9167)
    res = await svc.get_noise_level_audit(client_mock, 50.0633, 21.9169)
    assert res["exceeds_threshold"] is True
    assert res["noise_level_db"] >= 68.0
    assert "WYSOKI_HAŁAS" in res["zone"]


def test_compute_parcel_shape_metrics_regular():
    # 25m x 40m rectangular parcel in metric PUWG92
    pts = [
        (700000.0, 200000.0),
        (700025.0, 200000.0),
        (700025.0, 200040.0),
        (700000.0, 200040.0),
        (700000.0, 200000.0),
    ]
    metrics = GeoportalService.compute_parcel_shape_metrics(pts)
    assert metrics["front_width_m"] == 25.0
    assert metrics["length_m"] == 40.0
    assert metrics["aspect_ratio"] == 1.6
    assert metrics["shape_type"] == "REGULARNY"


def test_compute_parcel_shape_metrics_narrow_shoestring():
    # 12m x 70m narrow shoestring parcel
    pts = [
        (700000.0, 200000.0),
        (700012.0, 200000.0),
        (700012.0, 200070.0),
        (700000.0, 200070.0),
        (700000.0, 200000.0),
    ]
    metrics = GeoportalService.compute_parcel_shape_metrics(pts)
    assert metrics["front_width_m"] == 12.0
    assert metrics["length_m"] == 70.0
    assert metrics["aspect_ratio"] == 5.83
    assert metrics["shape_type"] == "WĄSKA_SZNUROWKA"


def test_compute_parcel_shape_metrics_insufficient():
    assert GeoportalService.compute_parcel_shape_metrics([(1.0, 2.0)])["front_width_m"] is None


@pytest.mark.asyncio
async def test_get_broadband_status_ftth():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "features": [
            {
                "properties": {
                    "id_statusu": 1,
                    "rodzaj_uslugi": "Dostęp do Internetu",
                    "medium": "Światłowód",
                    "nazwa_operatora": "Orange Polska",
                    "predkosc_pobierania": 1000,
                }
            }
        ]
    }
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_broadband_status(client_mock, 50.04, 22.0, "podkarpackie")
    assert res["status"] == "ŚWIATŁOWÓD_AKTYWNY"
    assert "Orange" in (res["details"] or "")


@pytest.mark.asyncio
async def test_get_broadband_status_planned_kpo():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "features": [
            {
                "properties": {
                    "id_statusu": 4,
                    "rodzaj_inwestycji": "KPO",
                    "planowany_termin_realizacji": "2026-Q4",
                }
            }
        ]
    }
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_broadband_status(client_mock, 50.04, 22.0, "podkarpackie")
    assert res["status"] == "PLANOWANY_KPO_FERC"
    assert "2026-Q4" in (res["details"] or "")


@pytest.mark.asyncio
async def test_get_broadband_status_none():
    svc = GeoportalService()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"features": []}
    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.return_value = mock_resp

    res = await svc.get_broadband_status(client_mock, 50.04, 22.0, "podkarpackie")
    assert res["status"] == "BRAK_ZASIĘGU"


@pytest.mark.asyncio
async def test_get_terrain_slope_and_aspect():
    svc = GeoportalService()

    # Elevation profile: center=200m, N=202m, S=198m, E=200m, W=200m
    def _mock_nmt(url, params=None, **kwargs):
        params = params or {}
        x = float(params.get("x", 0))
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        if x == 260025.0:  # North (+25m)
            resp.text = "202.0"
        elif x == 259975.0:  # South (-25m)
            resp.text = "198.0"
        else:
            resp.text = "200.0"
        return resp

    client_mock = AsyncMock(spec=httpx.AsyncClient)
    client_mock.get.side_effect = _mock_nmt

    res = await svc.get_terrain_slope_and_aspect(client_mock, 740000.0, 260000.0)
    assert res["slope_pct"] is not None
    assert res["slope_pct"] == 8.0  # (202 - 198) / 50m = 4/50 = 8.0%
    assert res["aspect"] == "POŁUDNIOWY"


def test_get_walkability_audit():
    svc = GeoportalService()
    # Coordinates near Rzeszów Główny (50.0415, 22.0050)
    res = svc.get_walkability_audit(50.0420, 22.0060)
    assert res["nearest_station"] == "Rzeszów Główny"
    assert res["distance_m"] < 250.0
    assert res["walk_time_min"] <= 4


@pytest.mark.asyncio
async def test_get_power_lines_risk():
    svc = GeoportalService()
    client_mock = AsyncMock(spec=httpx.AsyncClient)

    # Widełka 400kV line coordinates (~50.2033, ~21.9567)
    risk = await svc.get_power_lines_risk(client_mock, 50.2035, 21.9568, 710000.0, 280000.0)
    assert risk["risk"] != "BEZPIECZNIE"
    assert "Widełka" in risk["description"]
    assert risk["distance_m"] is not None and risk["distance_m"] < 150

    # Far away location
    safe = await svc.get_power_lines_risk(client_mock, 50.0100, 22.1500, 730000.0, 250000.0)
    assert safe["risk"] == "BEZPIECZNIE"
    assert safe["distance_m"] is None


@pytest.mark.asyncio
async def test_audit_location_selective_for_flats():
    svc = GeoportalService()

    # Mock get_parcel_by_xy
    svc.get_parcel_by_xy = AsyncMock(
        return_value={"parcel_id": "181601_1.0001.100/1", "commune": "Rzeszów", "county": "rzeszowski"}
    )
    svc.get_parcel_geometry_and_area = AsyncMock(
        return_value=(
            850.0,
            (720000.0, 260000.0),
            {"front_width_m": 12.0, "length_m": 70.0, "aspect_ratio": 5.8, "shape_type": "WĄSKA_SZNUROWKA"},
        )
    )
    svc.get_mpzp_info = AsyncMock(return_value={"zone": "MW: tereny mieszkaniowe", "status": "OBOWIĄZUJĄCY"})
    svc.get_flood_risk_isok = AsyncMock(return_value={"flood_zone": "BRAK"})
    svc.get_landslide_risk_sopo = AsyncMock(return_value={"risk": "BRAK"})
    svc.get_egib_full_audit = AsyncMock(
        return_value={"building_status": "UJAWNIONY", "soil_class": None, "is_protected_soil": False}
    )
    svc.get_gdos_protected_areas = AsyncMock(return_value={"is_protected": False})
    svc.get_nid_monuments = AsyncMock(return_value={"is_monument": False})
    svc.get_noise_level_audit = AsyncMock(
        return_value={"noise_level_db": 45.0, "zone": "DOPUSZCZALNY", "exceeds_threshold": False}
    )
    svc.get_gesut_networks = AsyncMock(return_value=None)
    svc.get_broadband_status = AsyncMock(return_value={"status": "FTTH"})
    svc.get_terrain_slope_and_aspect = AsyncMock(return_value={"slope_pct": 1.5, "aspect": "PŁASKI"})
    svc.get_power_lines_risk = AsyncMock(return_value={"risk": "BEZPIECZNIE"})

    # When auditing flat:
    res = await svc.audit_location(50.04, 22.01, radius_meters=120, category="mieszkanie")
    assert res["main_parcel_id"] == "181601_1.0001.100/1"
    # For flats, surrounding parcels scan is skipped (surrounding_parcels_count == 0)
    assert res["surrounding_parcels_count"] == 0
    # For flats, narrow parcel penalty should not be flagged in surrounding_risks
    assert not any("Wąska działka" in r for r in res["surrounding_risks"])
