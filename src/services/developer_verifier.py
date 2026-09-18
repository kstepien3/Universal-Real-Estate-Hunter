import re
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger

from src.services.spatial_cache import get_spatial_cache, set_spatial_cache


def validate_nip_checksum(nip_str: str) -> bool:
    """Validates 10-digit Polish NIP (Tax Identification Number) checksum using Modulo 11."""
    digits_only = re.sub(r"\D", "", nip_str)
    if len(digits_only) != 10:
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    checksum = sum(w * int(d) for w, d in zip(weights, digits_only[:9], strict=True)) % 11
    return checksum == int(digits_only[9])


def extract_tax_ids(text: str) -> dict[str, str | None]:
    """Extracts NIP, KRS, REGON numbers from listing descriptions and seller signatures."""
    if not text:
        return {"nip": None, "krs": None, "regon": None}

    # 1. Look for explicit NIP patterns (3-3-2-2, 3-2-2-3, 10-digit continuous, optional PL)
    nip: str | None = None
    labeled_nip_pattern = (
        r"(?:NIP|VAT[\s\-]ID|Tax[\s\-]ID)[\s:]*(?:PL)?[\s]*"
        r"([0-9]{3}[-\s]?[0-9]{3}[-\s]?[0-9]{2}[-\s]?[0-9]{2}|"
        r"[0-9]{3}[-\s]?[0-9]{2}[-\s]?[0-9]{2}[-\s]?[0-9]{3}|"
        r"[0-9]{10})\b"
    )
    for m in re.finditer(labeled_nip_pattern, text, re.IGNORECASE):
        cand = re.sub(r"\D", "", m.group(1))
        if validate_nip_checksum(cand):
            nip = cand
            break

    # 2. If no labeled NIP, scan for PL-prefixed 10-digit NIP or standalone 10-digit with valid checksum
    if not nip:
        pl_pattern = r"\bPL\s*([0-9]{10})\b"
        for m in re.finditer(pl_pattern, text, re.IGNORECASE):
            cand = m.group(1)
            if validate_nip_checksum(cand):
                nip = cand
                break

    # 3. KRS pattern (allows "KRS", "KRS nr", "nr KRS", optional padding)
    krs: str | None = None
    krs_pattern = r"\b(?:KRS[:\s\.]*(?:nr[:\s\.]*)?|nr\s+KRS[:\s\.]*)([0-9]{1,10})\b"
    krs_match = re.search(krs_pattern, text, re.IGNORECASE)
    if krs_match:
        krs = krs_match.group(1).zfill(10)

    # 4. REGON pattern
    regon: str | None = None
    regon_pattern = r"\b(?:REGON[:\s\.]*(?:nr[:\s\.]*)?|nr\s+REGON[:\s\.]*)([0-9]{9}|[0-9]{14})\b"
    regon_match = re.search(regon_pattern, text, re.IGNORECASE)
    if regon_match:
        regon = regon_match.group(1)

    return {"nip": nip, "krs": krs, "regon": regon}


def extract_company_name(text: str) -> str | None:
    """Extracts corporate seller / developer name (e.g. Sp. z o.o., S.A., Sp. k., Deweloper X)."""
    if not text:
        return None
    # 1. Company with Polish legal form (e.g. "Nowoczesne Osiedle Sp. z o.o.")
    legal_suffix = r"(?:Sp\.\s*z\s*o\.o\.|Spółka\s+z\s*o\.o\.|S\.A\.|Sp\.\s*k\.|Sp\.\s*j\.|Spółka\s+komandytowa)"
    corp_pattern = (
        rf"([A-ZĄĆĘŁŃÓŚŹŻ0-9][a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ0-9\.\-&]+"
        rf"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ0-9][a-ząćęłńóśźżA-ZĄĆĘŁŃÓŚŹŻ0-9\.\-&]+)*\s+{legal_suffix})"
    )
    m = re.search(corp_pattern, text)
    if m:
        cand = m.group(1).strip()
        cand = cand.split("\n")[-1].strip()
        if len(cand) >= 5 and not cand.lower().startswith(("sprzedaż", "oferta", "zakup", "niniejsza")):
            return cand

    # 2. "Deweloper: [Nazwa]" or "Biuro sprzedaży: [Nazwa]"
    labeled_pattern = r"(?:Deweloper|Inwestor|Biuro\s+sprzedaży|Biuro\s+nieruchomości|Agencja\s+nieruchomości)[\s:]+([A-ZĄĆĘŁŃÓŚŹŻ0-9][\w\s\.\-&]{2,40})"
    m2 = re.search(labeled_pattern, text, re.IGNORECASE)
    if m2:
        cand = m2.group(1).strip().split("\n")[0].strip()
        if len(cand) >= 3 and not cand.lower().startswith(("poleca", "zaprasza", "oferuje")):
            return cand
    return None


class DeveloperVerifierService:
    """
    Public intelligence verification of real estate developers and corporate sellers:
    1. Biała Lista VAT (Ministry of Finance - wl-api.mf.gov.pl):
       Verifies active VAT taxpayer status, formal company name, registered address, and KRS link.
    2. Open KRS API (Ministry of Justice - api-krs.ms.gov.pl):
       Audits corporate legal form, equity/share capital (kapitał zakładowy), corporate age,
       insolvency/liquidation status, and bailiff/arrears notices.
    """

    BIALA_LISTA_BASE = "https://wl-api.mf.gov.pl/api/search/nip"
    KRS_API_BASE = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny"

    def __init__(self, request_timeout: float = 5.0):
        self.timeout = request_timeout
        self.headers = {"User-Agent": "ApartmentHunter-DeveloperVerifier/1.0 (prop-tech-due-diligence; contact@local)"}

    async def verify_nip_biala_lista(
        self,
        client: httpx.AsyncClient | None,
        nip: str,
    ) -> dict[str, Any] | None:
        """Queries Ministry of Finance Biała Lista VAT for entity legal details and KRS."""
        if not validate_nip_checksum(nip):
            return None

        cache_key = f"mf:nip:{nip}"
        cached = await get_spatial_cache(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        url = f"{self.BIALA_LISTA_BASE}/{nip}?date={today_str}"

        try:
            if client is not None:
                resp = await client.get(url, headers=self.headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient() as new_client:
                    resp = await new_client.get(url, headers=self.headers, timeout=self.timeout)

            if resp.status_code == 200:
                data = resp.json()
                subject = (data.get("result") or {}).get("subject")
                if subject:
                    res = {
                        "name": subject.get("name"),
                        "nip": subject.get("nip"),
                        "status_vat": subject.get("statusVat"),
                        "regon": subject.get("regon"),
                        "krs": subject.get("krs"),
                        "residence_address": subject.get("residenceAddress"),
                        "working_address": subject.get("workingAddress"),
                        "registration_legal_date": subject.get("registrationLegalDate"),
                    }
                    await set_spatial_cache(cache_key, res, ttl_days=30)
                    return res
        except Exception as e:
            logger.debug(f"[DeveloperVerifier] Biała Lista VAT query note: {e}")

        return None

    async def verify_krs(
        self,
        client: httpx.AsyncClient | None,
        krs: str,
    ) -> dict[str, Any] | None:
        """Queries Ministry of Justice Open KRS API for corporate capital, status and proceedings."""
        clean_krs = re.sub(r"\D", "", krs).zfill(10)
        cache_key = f"ms:krs:{clean_krs}"
        cached = await get_spatial_cache(cache_key)
        if cached and isinstance(cached, dict):
            return cached

        url = f"{self.KRS_API_BASE}/{clean_krs}?rejestr=P&format=json"
        try:
            if client is not None:
                resp = await client.get(url, headers=self.headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient() as new_client:
                    resp = await new_client.get(url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                odpis = data.get("odpis", {})
                naglowek = odpis.get("naglowekA", {})
                dane = odpis.get("dane", {})
                dzial1 = dane.get("dzial1", {})
                dzial4 = dane.get("dzial4", {})
                dzial6 = dane.get("dzial6", {})

                # Extract legal form & name
                podmiot = dzial1.get("danePodmiotu", {})
                forma = podmiot.get("formaPrawna")
                nazwa = podmiot.get("nazwa")

                # Extract share capital
                kapital_info = dzial1.get("kapital", {}).get("wysokoscKapitaluZakladowego", {})
                kapital_wartosc: float | None = None
                if kapital_info:
                    raw_val = str(kapital_info.get("wartosc", "") or "").replace(" ", "").replace(",", ".")
                    try:
                        kapital_wartosc = float(raw_val)
                    except ValueError:
                        pass

                # Registration date
                data_rejestracji = naglowek.get("dataRejestracjiWKRS")
                reg_year: int | None = None
                if data_rejestracji and len(data_rejestracji) >= 4:
                    try:
                        reg_year = int(data_rejestracji[:4])
                    except ValueError:
                        pass

                # Check liquidation / bankruptcy / restructuring in Dział 6
                w_likwidacji = bool(dzial6.get("likwidacja") or dzial6.get("otwarcieLikwidacji"))
                w_upadlosci = bool(dzial6.get("postepowanieUpadlosciowe") or dzial6.get("ogloszenieUpadlosci"))
                postepowanie_ukladowe = bool(
                    dzial6.get("postepowanieUkladowe") or dzial6.get("postepowanieRestrukturyzacyjne")
                )

                # Arrears in Dział 4
                naleznosci = bool(dzial4.get("zaleglosci") or dzial4.get("wzmiankiONaleznosciach"))

                res = {
                    "krs": clean_krs,
                    "name": nazwa,
                    "legal_form": forma,
                    "capital_pln": kapital_wartosc,
                    "registration_date": data_rejestracji,
                    "registration_year": reg_year,
                    "is_in_liquidation": w_likwidacji,
                    "is_in_bankruptcy": w_upadlosci,
                    "is_in_restructuring": postepowanie_ukladowe,
                    "has_arrears_or_enforcement": naleznosci,
                }
                await set_spatial_cache(cache_key, res, ttl_days=30)
                return res
        except Exception as e:
            logger.debug(f"[DeveloperVerifier] KRS query note for {clean_krs}: {e}")

        return None

    def evaluate_risk(
        self,
        vat_data: dict[str, Any] | None,
        krs_data: dict[str, Any] | None,
    ) -> tuple[str, list[str]]:
        """
        Calculates risk level ('LOW', 'MEDIUM', 'HIGH') and Polish explanatory arguments.
        """
        reasons: list[str] = []
        is_high = False
        is_med = False

        current_year = datetime.now(UTC).year

        # 1. Insolvency / Liquidation
        if krs_data:
            if krs_data.get("is_in_bankruptcy"):
                is_high = True
                reasons.append("🚨 Podmiot znajduje się w stanie POSTĘPOWANIA UPADŁOŚCIOWEGO.")
            if krs_data.get("is_in_liquidation"):
                is_high = True
                reasons.append("🚨 Spółka jest W LIKWIDACJI — ryzyko niedokończenia inwestycji.")
            if krs_data.get("is_in_restructuring"):
                is_high = True
                reasons.append("⚠️ Spółka jest w trakcie postępowania restrukturyzacyjnego/układowego.")
            if krs_data.get("has_arrears_or_enforcement"):
                is_high = True
                reasons.append("⚠️ Wpis w Dziale 4 KRS: zgłoszono zaległości podatkowe/celne lub egzekucję komorniczą.")

            # 2. Capital analysis
            cap = krs_data.get("capital_pln")
            if cap is not None:
                if cap <= 5000.0:
                    is_med = True
                    reasons.append(
                        "⚠️ Minimalny ustawowy kapitał zakładowy (5 000 zł) — spółka celowa o zerowym buforze gwarancyjnym."
                    )
                elif cap >= 500000.0:
                    reasons.append(
                        f"✅ Wysoki kapitał zakładowy ({cap:,.0f} zł) zabezpieczający wypłacalność podmiotu."
                    )

            # 3. Market age
            reg_year = krs_data.get("registration_year")
            if reg_year:
                age = current_year - reg_year
                if age < 1:
                    is_med = True
                    reasons.append(
                        "⚠️ Nowo utworzona spółka (poniżej 1 roku na rynku) — brak udokumentowanej historii realizacji."
                    )
                elif age >= 5:
                    reasons.append(
                        f"✅ Doświadczony podmiot na rynku (rok rejestracji: {reg_year}, {age} lat działalności)."
                    )

        # 4. VAT status
        if vat_data:
            status_vat = (vat_data.get("status_vat") or "").strip().upper()
            if status_vat == "CZYNNY":
                reasons.append("✅ Status czynnego podatnika VAT potwierdzony w rejestrze MF.")
            elif status_vat in ("ZWOLNIONY", "NIEZAREJESTROWANY"):
                is_med = True
                reasons.append(f"⚠️ Podmiot nie jest czynnym podatnikiem VAT (status: {status_vat}).")

        if is_high:
            return "HIGH", reasons
        if is_med:
            return "MEDIUM", reasons
        if reasons:
            return "LOW", reasons

        return "NIEZNANE", ["Brak wystarczających danych rejestrowych do pełnej oceny ryzyka."]

    async def audit_developer(
        self,
        client: httpx.AsyncClient | str | None = None,
        description: str = "",
        seller_name: str | None = None,
        explicit_nip: str | None = None,
        is_private_owner: bool | None = None,
    ) -> dict[str, Any]:
        """
        Extracts identifiers from ad description/seller profile and performs due diligence audit.
        Accepts client as optional first argument or description directly.
        """
        http_client: httpx.AsyncClient | None = None
        if isinstance(client, str):
            # Positional usage: audit_developer(description)
            description = client
        elif client is not None:
            http_client = client
        combined_text = f"{seller_name or ''}\n{description or ''}"
        extracted = extract_tax_ids(combined_text)

        nip = explicit_nip or extracted.get("nip")
        krs = extracted.get("krs")

        # 1. Private individual sellers do not have KRS or company NIP
        if is_private_owner is True and not nip and not krs:
            return {
                "developer_name": None,
                "developer_nip": None,
                "developer_krs": None,
                "developer_capital_pln": None,
                "developer_registration_year": None,
                "developer_risk_level": "PRIVATE",
                "developer_risk_reasons": [
                    "Oferta bezpośrednia od osoby fizycznej (ogłoszenie prywatne — brak wpisu w KRS)."
                ],
            }

        vat_data: dict[str, Any] | None = None
        if nip:
            vat_data = await self.verify_nip_biala_lista(http_client, nip)
            if vat_data and not krs and vat_data.get("krs"):
                krs = vat_data["krs"]

        krs_data: dict[str, Any] | None = None
        if krs:
            krs_data = await self.verify_krs(http_client, krs)

        # Detect corporate company name from metadata or text if not fetched from registers
        detected_company = (
            (krs_data or {}).get("name")
            or (vat_data or {}).get("name")
            or seller_name
            or extract_company_name(combined_text)
        )

        if nip or krs:
            risk_level, risk_reasons = self.evaluate_risk(vat_data, krs_data)
        elif detected_company:
            risk_level = "BRAK_NIP"
            risk_reasons = [
                f"Zidentyfikowano podmiot: '{detected_company}', lecz w treści ogłoszenia brak numeru NIP/KRS do automatycznego audytu rejestrowego."
            ]
        elif is_private_owner is False:
            risk_level = "BRAK_NIP"
            risk_reasons = ["Ogłoszenie agencyjne/deweloperskie bez podanego numeru NIP/KRS."]
        else:
            risk_level = "BRAK_DANYCH"
            risk_reasons = ["Brak danych o deweloperze lub numerze NIP w ogłoszeniu."]

        return {
            "developer_name": detected_company,
            "developer_nip": nip,
            "developer_krs": krs,
            "developer_capital_pln": (krs_data or {}).get("capital_pln"),
            "developer_registration_year": (krs_data or {}).get("registration_year"),
            "developer_risk_level": risk_level,
            "developer_risk_reasons": risk_reasons,
        }


developer_verifier = DeveloperVerifierService()
