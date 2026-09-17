import re
from typing import Any, NamedTuple

from src.models.enums import (
    FinishCondition,
    HeatingType,
    MarketType,
    RoadType,
    SegmentSubtype,
    SewerageType,
)
from src.models.listing import ListingSchema


class Stage2AnalysisResult(NamedTuple):
    passed: bool
    rejection_reasons: list[str]
    pros: list[str]
    cons: list[str]
    detected_subtype: SegmentSubtype
    is_corner: bool
    has_parking_or_garage: bool
    detected_finish: FinishCondition
    has_visualisations: bool
    detected_sewerage: SewerageType
    detected_heating: HeatingType
    has_fiber: bool


class Stage2SemanticFilter:
    """
    Stage II: Semantic analysis of description (RegEx / heuristic NLP engine).
    1. Segment classification: IS_CORNER vs Middle segment.
       - If segment is middle and plot < 200 m² -> REJECT.
    2. Access road standard:
       - Rejects if explicitly 'droga polna', 'droga nieutwardzona', 'brak bezpośredniego zjazdu'.
    3. Parking spots & Garage:
       - Checks for garage or min. 2 parking spaces.
    4. Terrain verification:
       - Detects slope, embankment, clay soil, wetlands.
    5. Pros & Cons extraction for rich alert reporting.
    6. Finish condition & Visualisations detection.
    7. Utilities & Infrastructure: Sewerage (city vs septic), Heating (heat pump, gas, solid fuel), Fiber.
    """

    RE_CORNER = re.compile(
        r"\b(skrajn[yae]|skrajn\w*|narożn[yae]|narozn\w*|ostatni\s+w\s+rzędzie|ostatni\s+w\s+rzedzie)\b",
        re.IGNORECASE,
    )
    RE_MIDDLE = re.compile(
        r"\b(środkow[yae]|srodkow\w*|wewnętrzn[yae]|wewnetrzn\w*)\b",
        re.IGNORECASE,
    )

    RE_BAD_ROAD = re.compile(
        r"(dojazd\s+drog[aą]\s+poln[aą]|drog[aą]\s+nieutwardzon[aą]|droga\s+polna|droga\s+gruntowa|brak\s+bezpośredniego\s+zjazdu|brak\s+bezposredniego\s+zjazdu|dojazd\s+polny)",
        re.IGNORECASE,
    )

    RE_GOOD_ROAD = re.compile(
        r"(dojazd\s+asfaltow[ya]|droga\s+asfaltowa|kostk[aęi]|asfalt|bruk\b|zjazd\s+bezpośrednio\s+z\s+asfaltu)",
        re.IGNORECASE,
    )

    RE_GARAGE = re.compile(
        r"(garaż|garaz|w\s+bryle\s+budynku|wiata\s+garażowa|wiata\s+na\s+samochód)",
        re.IGNORECASE,
    )
    RE_PARKING = re.compile(
        r"(2\s+miejsc|dwa\s+miejsc|dwustanowiskow|podjazd\s+na\s+(?:dwa|2)\s+aut|parking\s+na\s+2|miejsca\s+postojowe)",
        re.IGNORECASE,
    )

    RE_TERRAIN_SLOPE_CLAY = re.compile(
        r"(spad(?:ek|ku)\s+terenu|strom[aey]|teren\s+nachylony|na\s+skarpie|skarp[aęie]|gliniast[aey]|podłoż[ue]\s+gliniast|podmokł[yae]|osuwisk[oa])",
        re.IGNORECASE,
    )

    RE_PLOT_EXTRACTION = re.compile(
        r"(?:działk[aię]|powierzchni[aą]\s+działki|ogród|ogrod(?:u|em)?|posesj[ai])\s*(?:o\s+(?:powierzchni|pow\.?))?\s*(?:ok\.?)?\s*(?:wynosi\s+)?(?:to\s+)?(\d+(?:[.,]\d+)?)\s*(ar[oó]w|ar[ay]?|a\b|m2|m²)",
        re.IGNORECASE,
    )

    RE_FINISH_DO_ZAMIESZKANIA = re.compile(
        r"(pod\s+klucz|wykończon\w*\s+pod\s+klucz|do\s+zamieszkania|do\s+wprowadzenia|w\s+pełni\s+wykończon\w*|gotow\w*\s+do\s+zamieszkania|po\s+remoncie|wysoki\s+standard\s+wykończenia)",
        re.IGNORECASE,
    )
    RE_FINISH_DEWELOPERSKI = re.compile(
        r"(stan(?:ie)?\s+dewelopersk\w*|dewelopersk(?:im|i|iego|im\s+plus|im\s+podwyższonym|im\s+premium))",
        re.IGNORECASE,
    )
    RE_FINISH_UNDER_CONSTRUCTION = re.compile(
        r"(w\s+trakcie\s+budowy|w\s+trakcie\s+realizacji|rozpoczęcie\s+budowy|planowany\s+termin\s+(?:oddania|zakończenia)|termin\s+(?:oddania|ukończenia|zakończenia|odbioru)|odbiór\s+w\s+(?:I|II|III|IV|\d)\s+kwartale|odbiór\s+budynku|zakończenie\s+inwestycji|zakończenie\s+prac\s+budowlanych|przewidywany\s+termin\s+oddania|oddanie\s+do\s+użytku\s+w\s+(?:202[5-9]|203\d))",
        re.IGNORECASE,
    )
    RE_FINISH_OPTION_UNDER_KEY = re.compile(
        r"(możliwość\s+wykończenia\s+pod\s+klucz|opcja\s+wykończenia\s+pod\s+klucz|za\s+dopłatą\s+pod\s+klucz|dopłat[aą]\s+do\s+stanu\s+pod\s+klucz|do\s+własnej\s+aranżacji|możliwość\s+doprowadzenia\s+do\s+stanu\s+pod\s+klucz)",
        re.IGNORECASE,
    )
    RE_FINISH_DO_WYKONCZENIA = re.compile(
        r"(do\s+wykończenia|do\s+wykonczenia|do\s+własnego\s+wykończenia|do\s+samodzielnego\s+wykończenia|wymaga\s+wykończenia|stan\s+do\s+wykończenia|częściowo\s+wykończon\w*|do\s+doprowadzenia\s+do\s+stanu\s+używalnośc\w*|do\s+dokończenia|wymaga\s+dokończenia|w\s+trakcie\s+wykończenia)",
        re.IGNORECASE,
    )
    RE_FINISH_SUROWY_ZAMKNIETY = re.compile(
        r"(stan(?:ie)?\s+surow(?:ym|y)?\s+zamknięt\w*|surowy\s+zamknięty|\bssz\b)",
        re.IGNORECASE,
    )
    RE_FINISH_SUROWY_OTWARTY = re.compile(
        r"(stan(?:ie)?\s+surow(?:ym|y)?\s+otwart\w*|surowy\s+otwarty|\bsso\b)",
        re.IGNORECASE,
    )
    RE_FINISH_DO_REMONTU = re.compile(
        r"(do\s+remontu|do\s+generalnego\s+remontu|do\s+odświeżenia|wymaga\s+(?:generalnego\s+)?remontu|do\s+częściowego\s+remontu)",
        re.IGNORECASE,
    )

    RE_VISUALISATIONS = re.compile(
        r"(wizualizacj\w*|zdjęci[ae]\s+poglądow\w*|zdjęcia\s+mają\s+charakter\s+poglądowy|przykładow\w*\s+(?:aranżacj\w*|wykończeni\w*|wystr[oó]j\w*)|aranżacja\s+wnętrz\s+(?:to\s+)?wizualizacj\w*|projekt\s+koncepcyjn\w*|koncepcj\w*\s+architektoniczn\w*|zdjęcia\s+przedstawiają\s+wizualizacj\w*|zdjęcia\s+(?:z\s+innej|z\s+poprzedniej|wzorcow\w*)\s+realizacji|dom\s+pokazow\w*|render\w*|model\s+3d|widok\s+3d|możliwość\s+wykończenia\s+pod\s+klucz\s+wg\s+(?:załączonych\s+)?wizualizacji|zdjęcia\s+wnętrz\s+są\s+(?:jedynie\s+)?inspiracją|inspiracja\s+wykończenia|zdjęcia\s+wnętrza\s+są\s+poglądowe)",
        re.IGNORECASE,
    )

    RE_SEWERAGE_SZAMBO = re.compile(
        r"(szamb[oaue]|zbiornik\s+bezodpływow\w*|szambo\s+szczeln\w*)",
        re.IGNORECASE,
    )
    RE_SEWERAGE_OCZYSZCZALNIA = re.compile(
        r"(przydomow\w*\s+oczyszczalni\w*|biologiczn\w*\s+oczyszczalni\w*|oczyszczalnia\s+ścieków)",
        re.IGNORECASE,
    )
    RE_SEWERAGE_MIEJSKA = re.compile(
        r"(kanalizacj[aey]\s+miejsk\w*|kanalizacj[aey]\s+gminn\w*|kanalizacj[aey]\s+sieciow\w*|sieć\s+kanalizacyjn\w*|podłączon\w*\s+do\s+kanalizacji|pełne\s+media\s+wraz\s+z\s+kanalizacją|\bkanalizacja\b)",
        re.IGNORECASE,
    )

    RE_HEATING_HEAT_PUMP = re.compile(
        r"(pomp[aey]\s+ciepła|pompa\s+ciepła|gruntow\w*\s+pomp\w*|powietrzn\w*\s+pomp\w*)",
        re.IGNORECASE,
    )
    RE_HEATING_GAS = re.compile(
        r"(ogrzewani\w*\s+gazow|piec\s+gazow|kocioł\s+gazow|gaz(?:em)?\s+sieciow|kondensacyjn\w*)",
        re.IGNORECASE,
    )
    RE_HEATING_SOLID = re.compile(
        r"(ekogrosz\w*|pellet\w*|paliw\w*\s+stał\w*|piec\s+na\s+węgiel|kopciuch)",
        re.IGNORECASE,
    )
    RE_HEATING_ELECTRIC = re.compile(
        r"(ogrzewani\w*\s+elektryczn|maty\s+grzewcz|folie\s+grzewcz)",
        re.IGNORECASE,
    )
    RE_FIBER = re.compile(
        r"(światłowód|swiatlowod|internet\s+światłowodowy|łącze\s+światłowodowe)",
        re.IGNORECASE,
    )
    RE_BALCONY_TERRACE = re.compile(
        r"(balkon\w*|taras\w*|loggi\w*|ogródek|ogrodkiem)",
        re.IGNORECASE,
    )
    RE_ELEVATOR = re.compile(
        r"(wind[aąę]|dźwig\s+osobow\w*|budynek\s+z\s+windą)",
        re.IGNORECASE,
    )
    RE_UTILITIES_PLOT = re.compile(
        r"(prąd|prad|skrzynka\s+elektryczn\w*|wodociąg\w*|wod[aą]\s+miejsk\w*|gaz\b|gazociąg\w*|kanalizacj\w*)",
        re.IGNORECASE,
    )

    def detect_sewerage(self, text: str, existing: SewerageType = SewerageType.NIEZNANA) -> SewerageType:
        """Detect sewerage type from text, prioritizing explicit szambo/oczyszczalnia markers."""
        if self.RE_SEWERAGE_SZAMBO.search(text):
            return SewerageType.SZAMBO
        if self.RE_SEWERAGE_OCZYSZCZALNIA.search(text):
            return SewerageType.OCZYSZCZALNIA
        if self.RE_SEWERAGE_MIEJSKA.search(text):
            return SewerageType.MIEJSKA
        return existing

    def detect_heating(self, text: str, existing: HeatingType = HeatingType.NIEZNANE) -> HeatingType:
        """Detect heating system from text, prioritizing heat pump and gas."""
        if self.RE_HEATING_HEAT_PUMP.search(text):
            return HeatingType.POMPA_CIEPLA
        if self.RE_HEATING_GAS.search(text):
            return HeatingType.GAZOWE
        if self.RE_HEATING_SOLID.search(text):
            return HeatingType.PELLET_WEGIEL
        if self.RE_HEATING_ELECTRIC.search(text):
            return HeatingType.ELEKTRYCZNE
        return existing

    def detect_fiber(self, text: str, existing: bool = False) -> bool:
        """Detect fiber optic internet availability."""
        return existing or bool(self.RE_FIBER.search(text))

    def detect_finish_condition(
        self, text: str, existing_finish: FinishCondition = FinishCondition.NIEOKRESLONY
    ) -> FinishCondition:
        """Detect finish condition via regex heuristics, prioritizing hard construction evidence."""
        # 1. Raw states
        if self.RE_FINISH_SUROWY_OTWARTY.search(text):
            return FinishCondition.SUROWY_OTWARTY
        if self.RE_FINISH_SUROWY_ZAMKNIETY.search(text):
            return FinishCondition.SUROWY_ZAMKNIETY

        # 2. Renovation needed
        if self.RE_FINISH_DO_REMONTU.search(text):
            return FinishCondition.DO_REMONTU

        # 3. Explicit developer state or unfinished or under construction / option for turnkey
        is_dev = bool(self.RE_FINISH_DEWELOPERSKI.search(text))
        is_to_finish = bool(self.RE_FINISH_DO_WYKONCZENIA.search(text))
        is_option_turnkey = bool(self.RE_FINISH_OPTION_UNDER_KEY.search(text))
        is_under_construction = bool(self.RE_FINISH_UNDER_CONSTRUCTION.search(text))

        if is_to_finish:
            return FinishCondition.DO_WYKONCZENIA
        if is_dev or is_option_turnkey or is_under_construction:
            return FinishCondition.DEWELOPERSKI

        # 4. Ready to use / turnkey (only if NOT overridden by developer/construction markers)
        if self.RE_FINISH_DO_ZAMIESZKANIA.search(text):
            return FinishCondition.DO_ZAMIESZKANIA

        return existing_finish

    def detect_visualisations(self, text: str) -> bool:
        """Detect if description or title indicates 3D renders or conceptual visualizations."""
        return bool(self.RE_VISUALISATIONS.search(text))

    def extract_plot_from_description(self, text: str) -> float | None:
        """Try to extract plot area from text if missing from header."""
        match = self.RE_PLOT_EXTRACTION.search(text)
        if match:
            raw_num = match.group(1).replace(",", ".")
            unit = match.group(2).lower()
            try:
                val = float(raw_num)
                if "ar" in unit or unit == "a":
                    return val * 100.0  # 1 ar = 100 m²
                return val
            except ValueError:
                pass
        return None

    def analyze(self, listing: ListingSchema, profile: Any | None = None) -> Stage2AnalysisResult:
        """
        Runs Stage II semantic evaluation according to property category.
        Returns:
            Stage2AnalysisResult(passed, rejection_reasons, pros, cons, detected_subtype, is_corner, has_parking, detected_finish, has_visualisations, detected_sewerage, detected_heating, has_fiber)
        """
        rejection_reasons: list[str] = []
        pros: list[str] = []
        cons: list[str] = []

        desc = f"{listing.title}\n{listing.raw_description}"
        desc_lower = desc.lower()

        category = "dom"
        if hasattr(listing, "category") and listing.category:
            cat_val = listing.category.value if hasattr(listing.category, "value") else str(listing.category)
            if cat_val in ("dom", "mieszkanie", "dzialka"):
                category = cat_val
        elif profile and hasattr(profile, "category"):
            category = profile.category

        detected_subtype = SegmentSubtype.NIEOKRESLONY
        is_corner = False
        has_parking_or_garage = False

        if category == "mieszkanie":
            # Apartment features
            if self.RE_BALCONY_TERRACE.search(desc):
                pros.append("Balkon / taras / loggia / ogródek")
            if self.RE_ELEVATOR.search(desc):
                pros.append("Winda w budynku")

            has_garage = bool(self.RE_GARAGE.search(desc))
            has_spaces = bool(self.RE_PARKING.search(desc))
            has_parking_or_garage = has_garage or has_spaces
            if has_parking_or_garage:
                pros.append("Miejsce postojowe / garaż / komórka")

        elif category == "dzialka":
            # Plot features
            if re.search(r"(prąd|prad|skrzynk\w*\s+elektryczn)", desc_lower):
                pros.append("Prąd w działce/drodze")
            if re.search(r"(wod\w*|wodociąg)", desc_lower):
                pros.append("Woda / wodociąg")
            if re.search(r"(gaz\b|gazociąg)", desc_lower):
                pros.append("Gaz")
            if re.search(r"(kanalizacj|ściek)", desc_lower):
                pros.append("Kanalizacja")

            bad_road_match = self.RE_BAD_ROAD.search(desc)
            if bad_road_match or listing.access_road_type == RoadType.POLNA:
                found_str = bad_road_match.group(0) if bad_road_match else "droga polna"
                cons.append(f"Nieutwardzony dojazd: '{found_str}'")
            elif self.RE_GOOD_ROAD.search(desc) or listing.access_road_type in (RoadType.ASFALT, RoadType.KOSTKA):
                pros.append("Utwardzony dojazd: asfalt / kostka")

        else:
            # House checks (dom)
            # 1. Segment subtype analysis
            is_corner = bool(self.RE_CORNER.search(desc))
            is_middle = bool(self.RE_MIDDLE.search(desc))

            if is_corner:
                detected_subtype = SegmentSubtype.SKRAJNY
                pros.append("Segment skrajny / narożny (większa prywatność i działka)")
            elif is_middle:
                detected_subtype = SegmentSubtype.SRODKOWY

            # Check effective plot area
            effective_plot = listing.area_plot
            if effective_plot is None or effective_plot <= 0:
                extracted_plot = self.extract_plot_from_description(desc)
                if extracted_plot:
                    effective_plot = extracted_plot
                    listing.area_plot = extracted_plot
                    pros.append(f"Wykryto metraż działki z opisu: ok. {effective_plot:.0f} m²")
                else:
                    cons.append("Brak jednoznacznej informacji o powierzchni działki w ogłoszeniu")

            # Segment Middle Rule: If segment is middle and plot < 200 m² -> REJECT
            if detected_subtype == SegmentSubtype.SRODKOWY:
                if effective_plot is not None and effective_plot < 200.0:
                    rejection_reasons.append(f"Segment środkowy z małą działką ({effective_plot:.0f} m² < 200 m²)")
                else:
                    cons.append("Segment środkowy (szeregówka)")
            elif effective_plot is not None and effective_plot < 200.0 and not is_corner:
                rejection_reasons.append(
                    f"Powierzchnia działki ({effective_plot:.0f} m²) poniżej bezwzględnego progu 200 m²"
                )

            # 2. Road access check
            bad_road_match = self.RE_BAD_ROAD.search(desc)
            if bad_road_match or listing.access_road_type == RoadType.POLNA:
                found_str = bad_road_match.group(0) if bad_road_match else "droga polna"
                rejection_reasons.append(f"Nieodpowiedni standard dojazdu: wykryto '{found_str}'")

            if self.RE_GOOD_ROAD.search(desc) or listing.access_road_type in (RoadType.ASFALT, RoadType.KOSTKA):
                pros.append("Utwardzony dojazd: asfalt / kostka")
            elif listing.access_road_type == RoadType.UTWARDZONA:
                pros.append("Dojazd drogą utwardzoną")

            # 3. Parking & Garage check
            has_garage = bool(self.RE_GARAGE.search(desc))
            has_2_spaces = bool(self.RE_PARKING.search(desc))
            has_parking_or_garage = has_garage or has_2_spaces

            if has_garage and has_2_spaces:
                pros.append("Garaż w bryle budynku + dedykowane miejsca postojowe")
            elif has_garage:
                pros.append("Garaż w bryle budynku")
            elif has_2_spaces:
                pros.append("Min. 2 miejsca postojowe / podjazd")
            else:
                cons.append("Brak bezpośredniej wzmianki o garażu lub min. 2 miejscach postojowych")

            # 4. Terrain slope & soil check
            terrain_match = self.RE_TERRAIN_SLOPE_CLAY.search(desc)
            if terrain_match:
                term = terrain_match.group(0)
                if any(k in term.lower() for k in ["osuwisk", "stromej skarp"]):
                    rejection_reasons.append(f"Zagrożenie geologiczne terenu: '{term}'")
                else:
                    cons.append(f"Weryfikacja ukształtowania terenu: wzmianka o '{term}'")

        # Additional equipment & quality features
        if re.search(r"(fotowoltaik|panele\s+pv)", desc_lower):
            pros.append("Instalacja fotowoltaiczna")
        if re.search(r"(ogrzewanie\s+podłogowe|podłogówk)", desc_lower):
            pros.append("Ogrzewanie podłogowe")
        if re.search(r"(rekuperacj)", desc_lower):
            pros.append("Rekuperacja (wentylacja mechaniczna)")
        if re.search(r"(rolety\s+zewnętrzne|rolety\s+podtynkowe)", desc_lower):
            pros.append("Rolety zewnętrzne")

        # Finish condition & Visualisations analysis
        # Hard evidence from description overrides false portal tags (e.g. developer marked as ready-to-use).
        existing_finish = getattr(listing, "finish_condition", FinishCondition.NIEOKRESLONY)
        desc_finish = self.detect_finish_condition(desc, FinishCondition.NIEOKRESLONY)

        has_visualisations = self.detect_visualisations(desc) or getattr(listing, "has_visualisations", False)

        # Check gallery image URLs for visualization clues
        if not has_visualisations and getattr(listing, "gallery_images", None):
            render_url_indicators = ("render", "wizualizac", "visualis", "koncepcj", "rzut", "projekt-3d")
            for u in listing.gallery_images:
                if any(ind in u.lower() for ind in render_url_indicators):
                    has_visualisations = True
                    break

        # Correlation check: Primary market + under construction / future delivery -> visualisations
        is_primary = getattr(listing, "market", None) == MarketType.PIERWOTNY
        year_built = getattr(listing, "year_built", None)
        is_future_or_current = year_built is not None and year_built >= 2025
        has_construction_marker = bool(self.RE_FINISH_UNDER_CONSTRUCTION.search(desc))
        if (
            is_primary
            and (is_future_or_current or has_construction_marker)
            and not has_visualisations
            and (self.RE_FINISH_DO_ZAMIESZKANIA.search(desc) or existing_finish == FinishCondition.DO_ZAMIESZKANIA)
        ):
            has_visualisations = True

        if (
            existing_finish
            and existing_finish != FinishCondition.NIEOKRESLONY
            and desc_finish != FinishCondition.NIEOKRESLONY
            and desc_finish != existing_finish
        ):
            detected_finish: FinishCondition
            if existing_finish == FinishCondition.DO_ZAMIESZKANIA and desc_finish in (
                FinishCondition.DEWELOPERSKI,
                FinishCondition.DO_WYKONCZENIA,
                FinishCondition.SUROWY_ZAMKNIETY,
                FinishCondition.SUROWY_OTWARTY,
                FinishCondition.DO_REMONTU,
            ):
                # Description facts override deceptive portal tag
                detected_finish = desc_finish
                cons.append(
                    f"⚠️ Skorygowano stan wykończenia: portal podaje '{existing_finish.value}', ale opis wykazuje stan '{desc_finish.value}'"
                )
            else:
                detected_finish = existing_finish
                cons.append(
                    f"⚠️ Rozbieżność stanu wykończenia: dane portalu '{existing_finish.value}', opis sugeruje '{desc_finish.value}'"
                )
        elif desc_finish != FinishCondition.NIEOKRESLONY:
            detected_finish = desc_finish
        else:
            detected_finish = existing_finish

        listing.finish_condition = detected_finish
        listing.has_visualisations = has_visualisations

        if detected_finish == FinishCondition.DO_ZAMIESZKANIA:
            pros.append("Standard wykończenia: gotowy do zamieszkania / pod klucz")
        elif detected_finish == FinishCondition.DEWELOPERSKI:
            pros.append("Stan deweloperski")
        elif detected_finish == FinishCondition.DO_WYKONCZENIA:
            cons.append("Do wykończenia (stan deweloperski — wymaga wykończenia wnętrz)")
        elif detected_finish == FinishCondition.SUROWY_ZAMKNIETY:
            cons.append("Stan surowy zamknięty (wymaga pełnego wykończenia)")
        elif detected_finish == FinishCondition.SUROWY_OTWARTY:
            cons.append("Stan surowy otwarty (brak stolarki i wykończenia)")
        elif detected_finish == FinishCondition.DO_REMONTU:
            cons.append("Wymaga remontu / odświeżenia")

        if has_visualisations:
            cons.append("⚠️ Oferta zawiera wizualizacje / zdjęcia poglądowe")

        # Utilities: Sewerage, Heating, Fiber
        existing_sewerage = getattr(listing, "sewerage", SewerageType.NIEZNANA)
        detected_sewerage = self.detect_sewerage(desc, existing_sewerage)

        existing_heating = getattr(listing, "heating", HeatingType.NIEZNANE)
        detected_heating = self.detect_heating(desc, existing_heating)

        has_fiber = self.detect_fiber(desc, getattr(listing, "has_fiber", False))

        if detected_sewerage == SewerageType.MIEJSKA:
            pros.append("Kanalizacja miejska/gminna")
        elif detected_sewerage == SewerageType.OCZYSZCZALNIA:
            pros.append("Przydomowa oczyszczalnia ścieków")
        elif detected_sewerage == SewerageType.SZAMBO:
            cons.append("⚠️ Szambo (brak kanalizacji miejskiej)")

        if detected_heating == HeatingType.POMPA_CIEPLA:
            pros.append("Pompa ciepła")
        elif detected_heating == HeatingType.GAZOWE:
            pros.append("Ogrzewanie gazowe")
        elif detected_heating == HeatingType.MIEJSKIE:
            pros.append("Ogrzewanie miejskie")
        elif detected_heating == HeatingType.PELLET_WEGIEL:
            cons.append("⚠️ Ogrzewanie na paliwo stałe (pellet/węgiel/drewno)")
        elif detected_heating == HeatingType.ELEKTRYCZNE:
            cons.append("Ogrzewanie elektryczne")

        if has_fiber:
            pros.append("Dostępny światłowód")

        # Check configuration rules
        try:
            from src.services.config_manager import config_manager

            cfg = profile or (
                config_manager.get_profile(listing.profile_name)
                if getattr(listing, "profile_name", None)
                else config_manager.get_config()
            )
            if not getattr(cfg, "allow_visualisations", True) and has_visualisations:
                rejection_reasons.append("Oferta oparta na wizualizacjach (wyłączone w konfiguracji)")
            allowed_fin = getattr(cfg, "allowed_finish_conditions", ["all"])
            if allowed_fin and "all" not in allowed_fin and detected_finish.value not in allowed_fin:
                rejection_reasons.append(f"Stan wykończenia '{detected_finish.value}' poza dozwolonymi w konfiguracji")
            if getattr(cfg, "reject_septic_tank", False) and detected_sewerage == SewerageType.SZAMBO:
                rejection_reasons.append("Oferta posiada szambo (wyłączone w konfiguracji)")
            allowed_heat = getattr(cfg, "allowed_heating_types", ["all"])
            if allowed_heat and "all" not in allowed_heat and detected_heating.value not in allowed_heat:
                rejection_reasons.append(f"Typ ogrzewania '{detected_heating.value}' poza dozwolonymi w konfiguracji")
        except Exception:
            pass

        passed = len(rejection_reasons) == 0
        return Stage2AnalysisResult(
            passed=passed,
            rejection_reasons=rejection_reasons,
            pros=pros,
            cons=cons,
            detected_subtype=detected_subtype,
            is_corner=is_corner,
            has_parking_or_garage=has_parking_or_garage,
            detected_finish=detected_finish,
            has_visualisations=has_visualisations,
            detected_sewerage=detected_sewerage,
            detected_heating=detected_heating,
            has_fiber=has_fiber,
        )
