// ============================================================================
// Due Diligence & AI Audit Module
// Owns: Land audit rendering, AI Due Diligence drawer, air quality breakdown,
// cadastral ID clipboard copy, negotiation strategy, and AI audit triggers.
// ============================================================================

function copyParcelCadastre(listingId) {
    const item = (typeof listingId === 'object' && listingId !== null)
        ? listingId
        : (allListings.find(i => i.id === listingId) || currentAiItem);
    if (!item || !item.parcel_id) return;
    navigator.clipboard.writeText(item.parcel_id).then(() => {
        showToast(`Skopiowano identyfikator działki: ${item.parcel_id}`);
    }).catch(() => {});
}

// ========================
// Due Diligence drawer content (land audit)
// ========================
function renderLandAuditHtml(item) {
    if (!item) return '';
    const audit = item.land_audit || {};
    const tco = audit.tco_audit || null;
    const commute = audit.commute_audit || null;
    const risk = audit.risk_shield || null;
    const gesut = audit.gesut_audit || null;
    const packet = audit.cadastral_packet || audit.search_packet || {
        parcel_id: item.parcel_id || '',
        parcel_short: (item.parcel_id || '').split('.').pop() || '',
        voivodeship: '',
        cadastral_area: item.cadastral_area || null
    };

    // 1. Legal & planning parameters
    let legalRows = '';
    if (item.parcel_id) {
        const areaTxt = item.cadastral_area ? ` (${item.cadastral_area} m²)` : '';
        legalRows += `
            <tr>
                <th>Identyfikator działki</th>
                <td class="value"><span class="num">${escapeHtml(item.parcel_id)}</span>${areaTxt}
                    <button type="button" class="copy-inline-btn" onclick="copyParcelCadastre(${item.id})" title="Kopiuj identyfikator do schowka">
                        ${svgIcon('copy', 10)} Kopiuj
                    </button>
                </td>
            </tr>`;
    }
    if (packet.voivodeship) {
        legalRows += `<tr><th>Województwo</th><td class="value">${escapeHtml(packet.voivodeship)}</td></tr>`;
    }
    if (item.mpzp_zone) {
        const isMnp = item.mpzp_status === 'OBOWIĄZUJĄCY';
        const hasWzInText = /wymagane wz|wymaga wz/i.test(item.mpzp_zone);
        const statusTag = isMnp
            ? `<span class="meta-tag tag-exact">Obowiązujący</span>`
            : (hasWzInText ? '' : `<span class="meta-tag tag-vis">Wymaga WZ</span>`);
        legalRows += `<tr class="${isMnp ? 'row-ok' : 'row-warn'}"><th>MPZP</th><td class="value">${escapeHtml(item.mpzp_zone)} ${statusTag}</td></tr>`;
    } else {
        legalRows += `<tr class="row-warn"><th>MPZP</th><td class="value">Brak planu miejscowego (wymagane WZ)</td></tr>`;
    }
    if (item.flood_risk_zone) {
        const isFlood = item.flood_risk_zone === 'ZAGROŻENIE_POWODZIOWE';
        legalRows += `<tr class="${isFlood ? 'row-danger' : 'row-ok'}"><th>Ryzyko powodziowe</th><td class="value">${isFlood ? 'Zagrożenie powodziowe (ISOK)' : 'Brak zagrożenia (teren bezpieczny powodziowo wg ISOK)'}</td></tr>`;
    }
    if (item.landslide_risk) {
        const isLandslide = item.landslide_risk !== 'BRAK' && item.landslide_risk !== 'NIEWYSTĘPUJE';
        const valText = isLandslide ? escapeHtml(item.landslide_risk) : 'Brak zagrożenia (teren nieosuwiskowy wg SOPO)';
        legalRows += `<tr class="${isLandslide ? 'row-danger' : 'row-ok'}"><th>Osuwiska (SOPO)</th><td class="value">${valText}</td></tr>`;
    }
    if (item.parcel_front_width_m) {
        const isNarrow = item.parcel_front_width_m < 16.0;
        let shapeLabel = item.parcel_shape_type ? item.parcel_shape_type.toLowerCase() : 'regularna';
        if (shapeLabel === 'regularny') shapeLabel = 'regularny kształt';
        const lengthTxt = item.parcel_length_m ? `, dł. ~${item.parcel_length_m} m` : '';
        legalRows += `<tr class="${isNarrow ? 'row-warn' : 'row-ok'}"><th>Front działki</th><td class="value"><span class="num">${item.parcel_front_width_m} m</span> (${escapeHtml(shapeLabel)}${escapeHtml(lengthTxt)})</td></tr>`;
    }
    if (item.parcel_aspect_ratio || item.parcel_shape_type) {
        const shape = (item.parcel_shape_type || '').toUpperCase();
        const ratio = item.parcel_aspect_ratio || 0;
        const isKiszka = shape.includes('SZNUROWKA') || shape.includes('WĄSKA') || ratio >= 4.0;
        let shapeDesc = item.parcel_shape_type || '—';
        if (shape === 'REGULARNY') shapeDesc = 'Regularne proporcje';
        if (item.parcel_aspect_ratio) shapeDesc += ` (proporcje 1:${item.parcel_aspect_ratio})`;
        legalRows += `<tr class="${isKiszka ? 'row-warn' : 'row-ok'}"><th>Proporcje działki</th><td class="value">${escapeHtml(shapeDesc)}${isKiszka ? ' — nieustawna działka, utrudniona zabudowa' : ''}</td></tr>`;
    }
    if (item.egib_soil_class) {
        const soil = String(item.egib_soil_class);
        const isProtected = /(?:^|[^A-Za-z])(?:R|Ł|Ps|S)(?:I{1,3}[ab]?)(?:$|[^A-Za-z])/i.test(soil);
        const isIndustrial = /(?:^|[^A-Za-z])(?:Ba|Bi)(?:$|[^A-Za-z])/.test(soil);
        const cls = (isProtected || isIndustrial) ? 'row-warn' : 'row-ok';
        let hint = '';
        if (isProtected) hint = ' — konieczność i koszt odrolnienia (klasy I–III)';
        else if (isIndustrial) hint = ' — sąsiedztwo przemysłowe';
        else if (/^B\b/i.test(soil.trim())) hint = ' — tereny mieszkaniowe';
        legalRows += `<tr class="${cls}"><th>Klasa gruntu EGiB</th><td class="value">${escapeHtml(soil)}${hint}</td></tr>`;
    }
    if (item.egib_building_status) {
        const st = String(item.egib_building_status).toUpperCase();
        const cls = st === 'UJAWNIONY' ? 'row-ok' : (st === 'BRAK_W_EWIDENCJI' ? 'row-danger' : 'row-warn');
        const desc = st === 'UJAWNIONY'
            ? 'Budynek ujawniony w kartotece budynków (odbiór PINB)'
            : (st === 'BRAK_W_EWIDENCJI' ? 'Brak w ewidencji — ryzyko samowoli / budowa w toku' : escapeHtml(item.egib_building_status));
        legalRows += `<tr class="${cls}"><th>Status budynku EGiB</th><td class="value">${desc}</td></tr>`;
    }
    if (item.noise_level_db !== null && item.noise_level_db !== undefined || item.noise_zone) {
        const db = (item.noise_level_db !== null && item.noise_level_db !== undefined) ? `${item.noise_level_db} dB Lden` : '';
        const zone = item.noise_zone ? escapeHtml(item.noise_zone) : '';
        const isHigh = (item.noise_level_db !== null && item.noise_level_db !== undefined && item.noise_level_db > 65) || /WYSOKI/i.test(item.noise_zone || '');
        const isNorm = /NORMATYWNY/i.test(zone) || (item.noise_level_db !== null && item.noise_level_db !== undefined && item.noise_level_db <= 55);
        const cls = isHigh ? 'row-danger' : (isNorm ? 'row-ok' : '');
        legalRows += `<tr class="${cls}"><th>Hałas GIOŚ</th><td class="value">${[db, zone].filter(Boolean).join(' · ') || '—'} (mapy akustyczne: drogi / tory / lotnisko)</td></tr>`;
    }
    if (item.nature_protected_zone) {
        legalRows += `<tr class="row-warn"><th>Obszary chronione GDOŚ</th><td class="value">${escapeHtml(item.nature_protected_zone)} (Natura 2000 / park krajobrazowy — ograniczenia)</td></tr>`;
    }
    if (item.monument_zone) {
        legalRows += `<tr class="row-danger"><th>Strefa konserwatorska NID</th><td class="value">${escapeHtml(item.monument_zone)} (restrykcje WKZ przy remontach)</td></tr>`;
    }
    if (item.cemetery_buffer_zone) {
        const cz = String(item.cemetery_buffer_zone).toUpperCase();
        const isSafe = cz === 'BRAK' || cz === 'BEZPIECZNIE' || cz === '>150M' || cz === 'POZA_STREFĄ';
        const cls = cz === '<50M' ? 'row-danger' : (cz === '50-150M' ? 'row-warn' : 'row-ok');
        const desc = cz === '<50M' ? 'Ograniczenia sanitarne <50 m (zakaz zabudowy/okien)'
            : (cz === '50-150M' ? 'Ograniczenia sanitarne 50–150 m (strefa ujęcia wody)'
            : (isSafe ? 'Brak ograniczeń sanitarnych (poza strefą cmentarną)' : escapeHtml(item.cemetery_buffer_zone)));
        legalRows += `<tr class="${cls}"><th>Strefa cmentarza</th><td class="value">${desc}</td></tr>`;
    }
    if (item.terrain_slope_pct !== null && item.terrain_slope_pct !== undefined) {
        const isSteep = item.terrain_slope_pct > 8.0;
        const aspectLabel = (item.terrain_aspect || 'płaska').toLowerCase().replace(/y$/, 'a').replace(/i$/, 'ia');
        legalRows += `<tr class="${isSteep ? 'row-warn' : 'row-ok'}"><th>Nachylenie terenu (NMT)</th><td class="value"><span class="num">${item.terrain_slope_pct}%</span> (ekspozycja ${escapeHtml(aspectLabel)})</td></tr>`;
    }
    if (item.broadband_status) {
        const isFtth = item.broadband_status === 'ŚWIATŁOWÓD_AKTYWNY';
        const isNone = item.broadband_status === 'BRAK_ZASIĘGU';
        const cls = isFtth ? 'row-ok' : (isNone ? 'row-warn' : '');
        let statusLabel = escapeHtml(item.broadband_status);
        if (isFtth) statusLabel = 'Światłowód aktywny';
        else if (isNone) statusLabel = 'Brak potwierdzonego zasięgu stacjonarnego';
        const details = item.broadband_details ? ` — ${escapeHtml(item.broadband_details)}` : '';
        legalRows += `<tr class="${cls}"><th>Światłowód (SIDUSIS)</th><td class="value">${statusLabel}${details}</td></tr>`;
    }
    if (item.power_lines_risk) {
        const isHv = /(LINIA|400KV|220KV|110KV|WN)/i.test(String(item.power_lines_risk));
        const isSafe = String(item.power_lines_risk).toUpperCase() === 'BEZPIECZNIE';
        const cls = isHv ? 'row-danger' : 'row-ok';
        const label = isSafe ? 'Bezpieczna odległość — brak linii WN w buforze 200 m' : escapeHtml(item.power_lines_risk);
        legalRows += `<tr class="${cls}"><th>Linie wysokiego napięcia</th><td class="value">${label}</td></tr>`;
    }
    if (item.walkability_pka_name) {
        const distKm = (item.walkability_pka_dist_m / 1000).toFixed(1);
        legalRows += `<tr><th>Stacja PKA</th><td class="value">${escapeHtml(item.walkability_pka_name)} (~${distKm} km)</td></tr>`;
    }
    if (item.air_aqi !== null && item.air_aqi !== undefined || item.air_pm25_heating_avg !== null && item.air_pm25_heating_avg !== undefined) {
        const aqiVal = (item.air_aqi !== null && item.air_aqi !== undefined) ? `AQI ${item.air_aqi} (${escapeHtml(item.air_aqi_label || '')})` : '';
        const heatVal = (item.air_pm25_heating_avg !== null && item.air_pm25_heating_avg !== undefined) ? `PM2.5 zima: ${item.air_pm25_heating_avg} µg/m³` : '';
        const summerVal = (item.air_pm25_summer_avg !== null && item.air_pm25_summer_avg !== undefined) ? `lato: ${item.air_pm25_summer_avg} µg/m³` : '';
        const giosVal = item.air_gios_station ? `Stacja GIOŚ: ${escapeHtml(item.air_gios_station)}${item.air_gios_dist_km ? ` (${item.air_gios_dist_km} km)` : ''}` : '';
        const risk = item.air_smog_risk || 'NISKIE';
        const cls = risk === 'WYSOKIE' ? 'row-danger' : (risk === 'SREDNIE' ? 'row-warn' : 'row-ok');
        const fullTxt = [aqiVal, [heatVal, summerVal].filter(Boolean).join(' vs '), giosVal].filter(Boolean).join(' · ');
        legalRows += `<tr class="${cls}"><th>Jakość powietrza (CAMS/GIOŚ)</th><td class="value">${fullTxt}</td></tr>`;
    }
    if (item.solar_energy_kwh_m2 || item.solar_hours_per_year) {
        const kwh = item.solar_energy_kwh_m2 ? `${item.solar_energy_kwh_m2} kWh/m²/rok` : '';
        const hrs = item.solar_hours_per_year ? `~${item.solar_hours_per_year} h słońca/rok` : '';
        const solarTxt = [kwh, hrs].filter(Boolean).join(' · ');
        legalRows += `<tr class="row-ok"><th>Potencjał solarny (PVGIS)</th><td class="value"><span class="num">${solarTxt}</span> (baza satelitarna SARAH-3)</td></tr>`;
    }
    if (item.geology_formation || item.geology_risk_note) {
        const isGeoWarn = Boolean(item.geology_risk_note && item.geology_risk_note.includes('⚠️'));
        legalRows += `<tr class="${isGeoWarn ? 'row-warn' : 'row-ok'}"><th>Warunki geologiczno-gruntowe</th><td class="value"><strong>${escapeHtml(item.geology_formation || 'Grunty mineralne')}</strong>${item.geology_risk_note ? `<div style="font-size:11px;color:var(--text-muted);margin-top:2px;">${escapeHtml(item.geology_risk_note)}</div>` : ''}</td></tr>`;
    }

    const legalHtml = legalRows ? `
        <div class="audit-block">
            <div class="audit-block-head">
                <div class="audit-block-title">Parametry prawne i planistyczne</div>
            </div>
            <table class="dd-table">${legalRows}</table>
        </div>
    ` : '';

    // 2. CAPEX (TCO) as clean financial table
    let tcoHtml = '';
    if (tco) {
        const breakdownRows = (tco.breakdown || []).map(b => `
            <tr>
                <td><strong>${escapeHtml(b.item)}</strong></td>
                <td class="amount">${formatPrice(b.amount)}</td>
                <td class="note">${escapeHtml(b.desc)}</td>
            </tr>
        `).join('');

        let negoNoteHtml = '';
        if (tco && tco.hidden_costs_total !== undefined && tco.hidden_costs_total !== null) {
            const finCost = (typeof tco.finishing_cost === 'number') ? tco.finishing_cost : 0;
            const txCost = (typeof tco.transaction_costs === 'number')
                ? tco.transaction_costs
                : Math.max(0, tco.hidden_costs_total - finCost);
            if (finCost > 0) {
                negoNoteHtml = `
                    <strong>Czynniki korygujące wycenę:</strong>
                    wykończenie wnętrz <strong class="num">+${formatPrice(finCost)}</strong>,
                    koszty transakcyjne (PCC, notariusz, prowizja) <strong class="num">+${formatPrice(txCost)}</strong>
                    — łącznie <strong class="num">+${formatPrice(tco.hidden_costs_total)}</strong> (+${tco.hidden_costs_pct}% ceny ofertowej).
                    Dane te stanowią podstawę argumentacji cenowej w negocjacjach.`;
            } else {
                negoNoteHtml = `
                    <strong>Brak nakładów na wykończenie</strong> (stan do zamieszkania) — koszty wejścia to wyłącznie
                    koszty transakcyjne (PCC, notariusz, prowizja): <strong class="num">+${formatPrice(txCost)}</strong>
                    (+${tco.hidden_costs_pct}% ceny ofertowej).
                    Dane te stanowią podstawę argumentacji cenowej w negocjacjach.`;
            }
        }

        const tcoVerdictClean = (tco.verdict || '').replace(/(\d),(\d{3})/g, '$1 $2');

        tcoHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Struktura kosztów całkowitych (CAPEX)</div>
                    <span class="audit-verdict-badge ${getSeverityBadgeClass(tco.severity)}">${escapeHtml(tcoVerdictClean || tco.verdict)}</span>
                </div>
                <table class="capex-table">
                    <thead>
                        <tr>
                            <th style="width:38%;">Pozycja kosztowa</th>
                            <th class="amount" style="width:24%;">Szacunek</th>
                            <th>Podstawa kalkulacji</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${breakdownRows}
                        <tr class="total">
                            <td>Suma nakładów kapitałowych</td>
                            <td class="amount">${formatPrice(tco.total_acquisition_cost)}</td>
                            <td class="note">Cena zakupu + podatki i opłaty + adaptacja</td>
                        </tr>
                    </tbody>
                </table>
                <div class="nego-note">${negoNoteHtml}</div>
                <div class="mortgage-sim-box">
                    <div class="mortgage-sim-title">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 7h10M7 11h10M7 15h4M15 15h2"/></svg>
                        <span>Kalkulator raty kredytu (symulacja finansowa)</span>
                    </div>
                    <div class="mortgage-grid">
                        <div class="mortgage-field">
                            <label for="mortgageBasePrice">Kwota bazowa (PLN)</label>
                            <input type="number" id="mortgageBasePrice" value="${Math.round(tco.total_acquisition_cost || item.price || 800000)}" step="10000" oninput="recalcMortgage()">
                        </div>
                        <div class="mortgage-field">
                            <label for="mortgageOwnPct">Wkład własny (%)</label>
                            <input type="number" id="mortgageOwnPct" value="20" min="10" max="90" step="5" oninput="recalcMortgage()">
                        </div>
                        <div class="mortgage-field">
                            <label for="mortgageYears">Okres spłaty</label>
                            <select id="mortgageYears" onchange="recalcMortgage()">
                                <option value="15">15 lat</option>
                                <option value="20">20 lat</option>
                                <option value="25" selected>25 lat</option>
                                <option value="30">30 lat</option>
                            </select>
                        </div>
                        <div class="mortgage-field">
                            <label for="mortgageRate">Oprocentowanie (%)</label>
                            <input type="number" id="mortgageRate" value="7.2" step="0.1" min="1" max="25" oninput="recalcMortgage()">
                        </div>
                    </div>
                    <div class="mortgage-results" id="mortgageResultsBox">
                        <div class="mortgage-res-item">
                            <span class="mortgage-res-label">Rata miesięczna</span>
                            <span class="mortgage-res-value" id="mortgageMonthlyPay">— zł</span>
                        </div>
                        <div class="mortgage-res-item">
                            <span class="mortgage-res-label">Kredyt / Wkład</span>
                            <span class="mortgage-res-sub" id="mortgageLoanAmount">—</span>
                        </div>
                        <div class="mortgage-res-item">
                            <span class="mortgage-res-label">Koszt odsetek</span>
                            <span class="mortgage-res-sub" id="mortgageTotalInterest">—</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    // 3. Commute
    let commuteHtml = '';
    if (commute) {
        const commuteFindings = (commute.findings || []).map(f => `
            <div class="audit-finding-item">
                <span class="audit-dot d-${f.severity === 'danger' ? 'danger' : (f.severity === 'warning' ? 'warning' : (f.severity === 'success' ? 'success' : 'info'))}"></span>
                <div class="audit-finding-body">
                    <div class="audit-finding-title">${f.badge ? `<span class="audit-finding-badge">${escapeHtml(f.badge)}</span>` : ''}${escapeHtml(f.title)}</div>
                    <div class="audit-finding-desc">${escapeHtml(f.desc)}</div>
                </div>
            </div>
        `).join('');

        const customCommute = item.commute_custom || {};
        const customEntries = Object.entries(customCommute);
        const customCommuteHtml = customEntries.length
            ? `<div class="custom-commute-box">
                    <div class="custom-commute-title">Twoje cele dojazdów (OSRM)</div>
                    <table class="dd-table">
                        <tbody>
                            ${customEntries.map(([label, v]) => `
                            <tr>
                                <th>${escapeHtml(label)}</th>
                                <td class="value"><span class="num">${Math.round(v.min)} min</span> · ${Number(v.km).toFixed(1)} km</td>
                            </tr>`).join('')}
                        </tbody>
                    </table>
               </div>`
            : '';

        commuteHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Dostępność komunikacyjna</div>
                    <span class="audit-verdict-badge ${getSeverityBadgeClass(commute.severity)}">${escapeHtml(commute.verdict)}</span>
                </div>
                <div class="audit-finding-list">${commuteFindings}</div>
                ${customCommuteHtml}
                <div class="audit-actions">
                    ${(item.latitude && item.longitude) ? `
                    <a href="https://www.google.com/maps/dir/?api=1&destination=${item.latitude},${item.longitude}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Wyznacz trasę dojazdu w Google Maps">
                        ${svgIcon('map-pin')} Nawiguj w Google Maps
                    </a>` : ''}
                    ${geoUrlOf(item) ? `
                    <a href="${escapeHtml(geoUrlOf(item))}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Pokaż w Geoportalu">
                        ${svgIcon('external')} Geoportal
                    </a>` : ''}
                </div>
            </div>
        `;
    }

    // 4. POI & 15-minute city audit (OpenStreetMap Overpass)
    let poiHtml = '';
    if (item.poi_counts || item.nearest_poi) {
        const counts = item.poi_counts || {};
        const nearest = item.nearest_poi || {};
        const labels = {
            'sklepy': 'Sklepy spożywcze',
            'apteki': 'Apteki',
            'edukacja': 'Szkoły / przedszkola',
            'zdrowie': 'Przychodnie / szpitale',
            'transport': 'Przystanki / stacje',
            'rekreacja': 'Parki / rekreacja'
        };
        const poiRows = Object.entries(labels).map(([cat, label]) => {
            const count = counts[cat] || 0;
            const near = nearest[cat];
            let detail = '';
            if (near && near.dist_m !== undefined) {
                detail = `najbliższy: <span class="num">${near.dist_m} m</span> (~${near.walk_min} min pieszo) — ${escapeHtml(near.name || '')}`;
            } else {
                detail = count > 0 ? `${count} w promieniu 1.5 km` : 'brak w promieniu 1.5 km';
            }
            return `<tr><th>${label}</th><td class="value"><strong class="num">${count}</strong> w 1.5 km &bull; ${detail}</td></tr>`;
        }).join('');

        poiHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Dostępność usług (15-minutowe miasto — OSM)</div>
                    <span class="audit-verdict-badge audit-verdict-success">Promień 1.5 km</span>
                </div>
                <table class="dd-table">${poiRows}</table>
            </div>
        `;
    }

    // 5. Legal & planning risk shield
    let riskHtml = '';
    if (risk) {
        const riskFindings = (risk.findings || []).map(f => `
            <div class="audit-finding-item">
                <span class="audit-dot d-${f.severity === 'danger' ? 'danger' : (f.severity === 'warning' ? 'warning' : (f.severity === 'success' ? 'success' : 'info'))}"></span>
                <div class="audit-finding-body">
                    <div class="audit-finding-title">${f.badge ? `<span class="audit-finding-badge">${escapeHtml(f.badge)}</span>` : ''}${escapeHtml(f.title)}</div>
                    <div class="audit-finding-desc">${escapeHtml(f.desc)}</div>
                </div>
            </div>
        `).join('');

        riskHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Ryzyka prawne i planistyczne</div>
                    <span class="audit-verdict-badge ${getSeverityBadgeClass(risk.severity)}">${escapeHtml(risk.verdict)}</span>
                </div>
                <div class="audit-finding-list">${riskFindings}</div>
                <div class="audit-actions">
                    ${item.parcel_id ? `
                    <button type="button" class="audit-copy-btn" onclick="copyParcelCadastre(${item.id})" title="Skopiuj identyfikator działki katastralnej">
                        ${svgIcon('copy', 11)} Kopiuj ID działki (${escapeHtml(packet.parcel_short || item.parcel_id)})
                    </button>` : ''}
                    ${geoUrlOf(item) ? `
                    <a href="${escapeHtml(geoUrlOf(item))}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Otwórz ewidencję gruntów EGiB">
                        ${svgIcon('external')} Ewidencja gruntów (EGiB)
                    </a>` : ''}
                </div>
            </div>
        `;
    }

    // 5. GESUT
    let gesutHtml = '';
    if (gesut) {
        const gesutFindings = (gesut.findings || []).map(f => `
            <div class="audit-finding-item">
                <span class="audit-dot d-${f.severity === 'danger' ? 'danger' : (f.severity === 'warning' ? 'warning' : (f.severity === 'success' ? 'success' : 'info'))}"></span>
                <div class="audit-finding-body">
                    <div class="audit-finding-title">${f.badge ? `<span class="audit-finding-badge">${escapeHtml(f.badge)}</span>` : ''}${escapeHtml(f.title)}</div>
                    <div class="audit-finding-desc">${escapeHtml(f.desc)}</div>
                </div>
            </div>
        `).join('');

        const gesutUrl = item.gesut_url || geoUrlOf(item);
        const gesutSource = gesut.source
            ? `<div class="gesut-source"><span class="audit-dot d-info"></span>${escapeHtml(gesut.source)}</div>`
            : '';

        gesutHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Uzbrojenie terenu (GESUT)</div>
                    <span class="audit-verdict-badge ${getSeverityBadgeClass(gesut.severity)}">${escapeHtml(gesut.verdict)}</span>
                </div>
                <div class="audit-finding-list">${gesutFindings}</div>
                ${gesutSource}
                <div class="audit-actions">
                    ${gesutUrl ? `
                    <a href="${escapeHtml(gesutUrl)}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Otwórz Geoportal z warstwami uzbrojenia terenu KIUT">
                        ${svgIcon('external')} Geoportal (uzbrojenie KIUT)
                    </a>` : ''}
                    ${geoUrlOf(item) ? `
                    <a href="${escapeHtml(geoUrlOf(item))}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Otwórz ewidencję gruntów EGiB">
                        ${svgIcon('external')} Ewidencja gruntów (EGiB)
                    </a>` : ''}
                </div>
                <div class="gesut-legend-bar">
                    <span class="gesut-legend-pill"><b>e</b> = prąd</span>
                    <span class="gesut-legend-pill"><b>g</b> = gaz</span>
                    <span class="gesut-legend-pill"><b>w</b> = woda</span>
                    <span class="gesut-legend-pill"><b>k</b> = kanalizacja</span>
                    <span class="gesut-legend-pill"><b>t</b> = światłowód</span>
                </div>
            </div>
        `;
    }

    // 6. GUNB building permits (200 m radius)
    let gunbHtml = '';
    if (item.gunb_status || (item.gunb_permits && item.gunb_permits.length) || (item.gunb_risk_flags && item.gunb_risk_flags.length)) {
        const gunbStatus = item.gunb_status || 'BRAK_DANYCH';
        const gunbCls = /RYZYKO/.test(gunbStatus) ? 'row-danger' : (/BRAK_DANYCH/.test(gunbStatus) ? '' : 'row-ok');
        const gunbBadge = /RYZYKO/.test(gunbStatus) ? 'audit-verdict-danger' : (/BRAK_DANYCH/.test(gunbStatus) ? 'audit-verdict-warning' : 'audit-verdict-success');
        const gunbPermits = (item.gunb_permits || []).slice(0, 5).map(p => {
            const nr = escapeHtml(p.numer_decyzji || p.numer || '—');
            const zam = escapeHtml(p.nazwa_zamierzenia || p.zamierzenie || '');
            return `<tr><th>${nr}</th><td class="value">${zam}</td></tr>`;
        }).join('');
        const gunbFlags = (item.gunb_risk_flags || []).map(f => `<tr class="row-danger"><th>Ryzyko</th><td class="value">${escapeHtml(f)}</td></tr>`).join('');
        gunbHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Pozwolenia na budowę w sąsiedztwie (GUNB/RWDZ, 200 m)</div>
                    <span class="audit-verdict-badge ${gunbBadge}">${escapeHtml(gunbStatus)}</span>
                </div>
                <table class="dd-table">
                    ${gunbFlags || (gunbPermits ? '' : `<tr class="${gunbCls}"><th>Status</th><td class="value">Brak danych o pozwoleniach w rejestrze</td></tr>`)}
                    ${gunbPermits}
                </table>
                ${item.gunb_url ? `
                <div class="audit-actions">
                    <a href="${escapeHtml(item.gunb_url)}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Otwórz wyszukiwarkę GUNB dla tej działki">
                        ${svgIcon('external')} Wyszukiwarka GUNB
                    </a>
                </div>` : ''}
            </div>
        `;
    }

    // 7. Vision AI photo audit (Living Quarters)
    let visionHtml = '';
    if (item.vision_finish_condition) {
        const isUnknown = item.vision_finish_condition === 'NIEZNANY';
        const vRow = item.vision_is_render === true ? 'row-warn' : (isUnknown ? 'row-neutral' : 'row-ok');
        const vBadge = item.vision_is_render === true ? 'audit-verdict-warning' : (isUnknown ? 'audit-verdict-muted' : 'audit-verdict-success');
        const formatFinishConditionLabel = (val) => {
            if (!val) return '';
            const norm = String(val).trim().toUpperCase();
            const map = {
                'DO_ZAMIESZKANIA': 'Do zamieszkania',
                'DO_WYKONCZENIA': 'Do wykończenia',
                'DEWELOPERSKI': 'Stan deweloperski',
                'SUROWY': 'Stan surowy',
                'DO_REMONTU': 'Do remontu',
                'NIEZNANY': 'Nieznany'
            };
            if (map[norm]) return map[norm];
            return norm.replace(/_/g, ' ').toLowerCase().replace(/^\w/, c => c.toUpperCase());
        };
        const vConditionLabel = formatFinishConditionLabel(item.vision_finish_condition);
        const vVerdict = item.vision_is_render === true ? 'WIZUALIZACJE 3D' : vConditionLabel;
        const fp = item.vision_floorplan_details || {};
        const formatVisionDefect = (d) => {
            if (!d) return '';
            if (typeof d === 'string') return d;
            if (typeof d === 'object') {
                const desc = d.description || d.defect || d.defect_type || d.wada || d.note || d.name || '';
                const photo = (d.photo_id !== undefined && d.photo_id !== null)
                    ? d.photo_id
                    : ((d.photo_index !== undefined && d.photo_index !== null)
                        ? d.photo_index
                        : ((d.image_index !== undefined && d.image_index !== null)
                            ? d.image_index
                            : ((d.image_id !== undefined && d.image_id !== null) ? d.image_id : null)));
                const prefix = photo !== null ? `[Zdjęcie ${photo}] ` : '';
                return (prefix + (desc || Object.values(d).filter(v => typeof v === 'string' || typeof v === 'number').join(' — '))).trim();
            }
            return String(d);
        };
        const defects = (item.vision_defects || []).map(d => `<tr><th>Wada</th><td class="value">${escapeHtml(formatVisionDefect(d))}</td></tr>`).join('');
        const summaryHtml = item.vision_summary ? `
            <div class="audit-summary-note" style="margin: 8px 0 12px 0; padding: 10px 14px; background: rgba(59, 130, 246, 0.08); border-left: 3px solid var(--accent, #3b82f6); border-radius: 4px; font-size: 13px; line-height: 1.5; color: var(--text, #e2e8f0);">
                <span style="font-size: 14px; margin-right: 4px;">💬</span> <em>${escapeHtml(item.vision_summary)}</em>
            </div>
        ` : '';
        const discrepancyRow = item.vision_discrepancy_note ? `
            <tr class="row-warn">
                <th>Rozbieżność z opisem</th>
                <td class="value"><span style="color: var(--yellow, #f59e0b); font-weight: 600;">⚠️ ${escapeHtml(item.vision_discrepancy_note)}</span></td>
            </tr>
        ` : '';
        visionHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Audyt zdjęć (Vision AI)</div>
                    <span class="audit-verdict-badge ${vBadge}">${escapeHtml(vVerdict)}</span>
                </div>
                ${summaryHtml}
                <table class="dd-table">
                    <tr class="${vRow}"><th>Stan ze zdjęć</th><td class="value">${escapeHtml(vConditionLabel)}${item.vision_is_render === true ? ' — zdjęcia to rendery, stan faktyczny do weryfikacji na żywo' : ''}</td></tr>
                    ${discrepancyRow}
                    ${fp.orientation ? `<tr><th>Rzut: orientacja</th><td class="value">${escapeHtml(fp.orientation)}</td></tr>` : ''}
                    ${fp.usability_score ? `<tr><th>Rzut: ustawność</th><td class="value"><span class="num">${fp.usability_score}/10</span>${fp.room_layout_notes ? ` — ${escapeHtml(fp.room_layout_notes)}` : ''}</td></tr>` : ''}
                    ${defects}
                </table>
            </div>
        `;
    }

    // 8. Developer / KRS background check
    let developerHtml = '';
    if (item.developer_name || item.developer_nip || item.developer_krs || item.developer_risk_level || item.is_private_owner) {
        const devLevel = (item.developer_risk_level || (item.is_private_owner ? 'PRIVATE' : 'NIEZNANE')).toUpperCase();
        let devBadge = 'audit-verdict-warning';
        let devCls = 'row-warn';
        let devLabel = devLevel;

        if (devLevel === 'PRIVATE') {
            devBadge = 'audit-verdict-info';
            devCls = 'row-ok';
            devLabel = 'OFERTA PRYWATNA';
        } else if (devLevel === 'LOW') {
            devBadge = 'audit-verdict-success';
            devCls = 'row-ok';
            devLabel = 'NISKIE RYZYKO';
        } else if (devLevel === 'HIGH') {
            devBadge = 'audit-verdict-danger';
            devCls = 'row-danger';
            devLabel = 'WYSOKIE RYZYKO';
        } else if (devLevel === 'MEDIUM') {
            devBadge = 'audit-verdict-warning';
            devCls = 'row-warn';
            devLabel = 'ŚREDNIE RYZYKO';
        } else if (devLevel === 'BRAK_NIP') {
            devBadge = 'audit-verdict-warning';
            devCls = 'row-warn';
            devLabel = 'BRAK NIP (DO WERYFIKACJI)';
        } else {
            devBadge = 'audit-verdict-warning';
            devCls = 'row-warn';
            devLabel = 'NIEZWERYFIKOWANY';
        }

        const devReasons = (item.developer_risk_reasons || []).map(r => `<tr class="${devLevel === 'LOW' || devLevel === 'PRIVATE' ? 'row-ok' : 'row-warn'}"><th>Ocena</th><td class="value">${escapeHtml(r)}</td></tr>`).join('');

        let searchLink = null;
        let searchLabel = 'Wyszukiwarka KRS';
        if (item.developer_krs) {
            searchLink = `https://rejestr.io/krs?q=${encodeURIComponent(item.developer_krs)}`;
            searchLabel = `KRS: ${escapeHtml(item.developer_krs)}`;
        } else if (item.developer_nip) {
            searchLink = `https://rejestr.io/krs?q=${encodeURIComponent(item.developer_nip)}`;
            searchLabel = `Szukaj NIP: ${escapeHtml(item.developer_nip)}`;
        } else if (item.developer_name && devLevel !== 'PRIVATE') {
            searchLink = `https://rejestr.io/krs?q=${encodeURIComponent(item.developer_name)}`;
            searchLabel = `Szukaj podmiotu w KRS / Rejestr.io`;
        }

        developerHtml = `
            <div class="audit-block">
                <div class="audit-block-head">
                    <div class="audit-block-title">Deweloper / sprzedawca (KRS)</div>
                    <span class="audit-verdict-badge ${devBadge}">${escapeHtml(devLabel)}</span>
                </div>
                <table class="dd-table">
                    ${item.developer_name ? `<tr><th>Podmiot / Sprzedawca</th><td class="value">${escapeHtml(item.developer_name)}</td></tr>` : (item.is_private_owner ? `<tr><th>Typ sprzedaży</th><td class="value">Bezpośrednio od właściciela (osoba fizyczna)</td></tr>` : '')}
                    ${item.developer_nip ? `<tr><th>NIP</th><td class="value"><span class="num">${escapeHtml(item.developer_nip)}</span></td></tr>` : ''}
                    ${item.developer_krs ? `<tr><th>KRS</th><td class="value"><span class="num">${escapeHtml(item.developer_krs)}</span></td></tr>` : ''}
                    ${item.developer_capital_pln ? `<tr><th>Kapitał zakładowy</th><td class="value"><span class="num">${Number(item.developer_capital_pln).toLocaleString('pl-PL')} zł</span></td></tr>` : ''}
                    ${item.developer_registration_year ? `<tr><th>Rok rejestracji</th><td class="value"><span class="num">${item.developer_registration_year}</span></td></tr>` : ''}
                    <tr class="${devCls}"><th>Status weryfikacji</th><td class="value">${escapeHtml(devLabel)}</td></tr>
                    ${devReasons}
                </table>
                ${searchLink ? `
                <div class="audit-actions" style="margin-top:8px;">
                    <a href="${searchLink}" target="_blank" rel="noopener noreferrer" class="audit-link-btn" title="Sprawdź podmiot w bazie Rejestr.io / KRS">
                        ${svgIcon('external')} ${searchLabel}
                    </a>
                </div>` : ''}
            </div>
        `;
    }

    return `
        ${legalHtml}
        ${tcoHtml}
        ${commuteHtml}
        ${poiHtml}
        ${gunbHtml}
        ${visionHtml}
        ${developerHtml}
        ${riskHtml}
        ${gesutHtml}
    `;
}

function geoUrlOf(item) {
    if (item.geoportal_url) return item.geoportal_url;
    if (item.latitude && item.longitude) {
        return `https://mapy.geoportal.gov.pl/imap/Imgp_2.html?locale=pl&gui=new&session=%7B%22actions%22%3A%5B%7B%22name%22%3A%22locatePoint%22%2C%22params%22%3A%7B%22x%22%3A${item.longitude}%2C%22y%22%3A${item.latitude}%2C%22srid%22%3A4326%7D%7D%5D%7D`;
    }
    return null;
}

function getSeverityBadgeClass(sev) {
    if (sev === 'danger') return 'audit-verdict-danger';
    if (sev === 'warning') return 'audit-verdict-warning';
    return 'audit-verdict-success';
}

// ========================
// Due Diligence drawer
// ========================
let currentAiItem = null;

async function openAiModal(listingId, forceRefresh = false) {
    const item = allListings.find(i => i.id === listingId);
    if (!item) return;
    currentAiItem = item;

    if (item.user_status === 'NEW') {
        updateStatus(item.id, 'CHECKED');
    }

    document.getElementById('aiModalTitle').innerText = item.title || '';

    // Lazy-load the full audit payload (land_audit, negotiation_arguments).
    if (!item._detailLoaded || forceRefresh) {
        try {
            const full = await Transport.listingDetail(listingId);
            if (full && typeof full === 'object') {
                Object.assign(item, full);
                item._detailLoaded = true;
            }
        } catch (err) {
            console.warn('Detail fetch failed, falling back to summary data:', err);
        }
    }

    // Recommendation
    const verdictSection = document.getElementById('aiVerdictSection');
    const verdictBadge = document.getElementById('aiVerdictBadge');
    const verdictContent = document.getElementById('aiVerdictContent');
    if (item.ai_verdict || item.worth_interest !== null && item.worth_interest !== undefined) {
        verdictSection.style.display = 'flex';
        if (item.worth_interest === true) {
            verdictBadge.className = 'verdict-badge positive';
            verdictBadge.innerHTML = '<span class="verdict-dot"></span>Rekomendacja: Pozytywna (Kwalifikuje się)';
        } else if (item.worth_interest === false) {
            verdictBadge.className = 'verdict-badge negative';
            verdictBadge.innerHTML = '<span class="verdict-dot"></span>Rekomendacja: Negatywna (Do odrzucenia)';
        } else {
            verdictBadge.className = 'verdict-badge unknown';
            verdictBadge.innerHTML = '<span class="verdict-dot"></span>Rekomendacja: Wymaga weryfikacji';
        }
        verdictContent.innerText = item.ai_verdict || 'Brak uzasadnienia rekomendacji.';
    } else {
        verdictSection.style.display = 'none';
    }

    // Update AI audit button state
    const btnAiAudit = document.getElementById('btnGenerateAiAudit');
    if (btnAiAudit) {
        btnAiAudit.innerText = item.ai_summary ? '🔄 Odśwież raport AI' : '🤖 Generuj raport AI';
        btnAiAudit.disabled = false;
    }

    // Synthesis
    const summaryEl = document.getElementById('aiSummaryContent');
    if (item.ai_summary) {
        summaryEl.innerText = item.ai_summary;
    } else {
        summaryEl.innerHTML = `
            <div style="display:flex;flex-direction:column;gap:8px;padding:10px 12px;background:var(--surface-2);border-radius:var(--r-md);border:1px dashed var(--border);">
                <span style="color:var(--text-muted);font-size:var(--font-size-xs);">Oferta nie posiada jeszcze wygenerowanego raportu AI.</span>
                <button class="btn btn-sm btn-ai-audit" style="align-self:flex-start;" onclick="triggerAiAuditForCurrentItem()">
                    🤖 Generuj raport AI teraz
                </button>
            </div>
        `;
    }

    // Spatial / financial / legal / vision intelligence audit
    const spatialSection = document.getElementById('aiSpatialSection');
    const spatialContent = document.getElementById('aiSpatialContent');
    const hasSpatialOrIntel = Boolean(
        item.parcel_id
        || item.mpzp_zone
        || item.flood_risk_zone
        || item.geoportal_url
        || (item.latitude && item.longitude)
        || item.land_audit
        || item.vision_finish_condition
        || (item.gunb_permits && item.gunb_permits.length)
        || (item.gunb_risk_flags && item.gunb_risk_flags.length)
        || item.developer_name
        || item.developer_nip
        || item.developer_risk_level
        || item.is_private_owner
        || (item.commute_drive_min !== null && item.commute_drive_min !== undefined)
    );
    if (hasSpatialOrIntel) {
        spatialSection.style.display = 'flex';
        spatialContent.innerHTML = renderLandAuditHtml(item);
        recalcMortgage();
    } else {
        spatialSection.style.display = 'none';
    }

    // Contact
    const contactSection = document.getElementById('aiContactSection');
    if (item.contact_phone || item.contact_person) {
        contactSection.style.display = 'flex';
        const phoneEl = document.getElementById('aiContactPhone');
        const personEl = document.getElementById('aiContactPerson');
        if (item.contact_phone) {
            const cleanPhone = item.contact_phone.replace(/[\s-]/g, '');
            phoneEl.href = 'tel:' + cleanPhone;
            phoneEl.innerText = item.contact_phone;
        } else {
            phoneEl.href = '';
            phoneEl.innerText = '';
        }
        personEl.innerText = item.contact_person || '';
    } else {
        contactSection.style.display = 'none';
    }

    // 1. Structured Risks Table
    const riskSection = document.getElementById('aiStructuredRisksSection');
    const riskContent = document.getElementById('aiStructuredRisksContent');
    const risks = item.structured_risks || [];
    if (riskSection && riskContent) {
        if (risks.length > 0) {
            riskSection.style.display = 'flex';
            const riskCards = risks.map(r => {
                const sev = (r.severity || 'SREDNIE').toUpperCase();
                let badgeCls = 'audit-verdict-warning';
                if (sev.includes('KRYT') || sev.includes('WYSOK')) badgeCls = 'audit-verdict-danger';
                else if (sev.includes('NISK')) badgeCls = 'audit-verdict-success';

                return `
                    <div style="background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r-sm);padding:8px 10px;display:flex;flex-direction:column;gap:4px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <strong style="font-size:var(--font-size-xs);color:var(--text-strong);">${escapeHtml(r.risk || r.category || 'Zidentyfikowane ryzyko')}</strong>
                            <span class="audit-verdict-badge ${badgeCls}" style="font-size:10px;padding:1px 6px;">${escapeHtml(sev)}</span>
                        </div>
                        <div style="font-size:var(--font-size-xs);color:var(--text-secondary);">${escapeHtml(r.impact || r.description || '')}</div>
                        ${r.action ? `<div style="font-size:var(--font-size-xs);color:var(--blue-text);background:var(--blue-bg);padding:4px 8px;border-radius:var(--r-xs);margin-top:2px;"><strong>Zalecane działanie:</strong> ${escapeHtml(r.action)}</div>` : ''}
                    </div>
                `;
            }).join('');
            riskContent.innerHTML = `<div style="display:flex;flex-direction:column;gap:6px;">${riskCards}</div>`;
        } else {
            riskSection.style.display = 'none';
        }
    }

    // 2. Documents checklist
    const docSection = document.getElementById('aiDocumentsSection');
    const docList = document.getElementById('aiDocumentsList');
    const docs = item.documents_to_obtain || [];
    if (docSection && docList) {
        if (docs.length > 0) {
            docSection.style.display = 'flex';
            docList.innerHTML = docs.map((d, idx) => `
                <li style="display:flex;align-items:flex-start;gap:8px;padding:4px 0;">
                    <input type="checkbox" id="doc_check_${item.id}_${idx}" style="margin-top:3px;cursor:pointer;" />
                    <label for="doc_check_${item.id}_${idx}" style="cursor:pointer;font-size:var(--font-size-xs);color:var(--text-primary);">${escapeHtml(d)}</label>
                </li>
            `).join('');
        } else {
            docSection.style.display = 'none';
        }
    }

    // 3. Questions (categorized by stakeholder or fallback to flat list)
    const qList = document.getElementById('aiQuestionsList');
    const sq = item.stakeholder_questions || {};
    const roleLabels = {
        'seller': 'Sprzedający / Pośrednik',
        'community': 'Zarządca / Wspólnota',
        'notary': 'Kancelaria Notarialna',
        'municipality': 'Wydział Architektury / Urząd Gminy'
    };
    const hasStructuredQuestions = Object.values(sq).some(arr => Array.isArray(arr) && arr.length > 0);

    if (hasStructuredQuestions) {
        let sqHtml = '';
        for (const [role, list] of Object.entries(sq)) {
            if (Array.isArray(list) && list.length > 0) {
                const title = roleLabels[role] || role;
                sqHtml += `<li style="list-style:none;margin-top:8px;margin-bottom:4px;"><strong style="font-size:var(--font-size-xs);color:var(--blue-text);text-transform:uppercase;letter-spacing:0.5px;">📌 ${escapeHtml(title)}:</strong></li>`;
                sqHtml += list.map(q => `<li>${escapeHtml(q)}</li>`).join('');
            }
        }
        qList.innerHTML = sqHtml;
    } else {
        const questions = item.ai_questions || [];
        if (questions.length > 0) {
            qList.innerHTML = questions.map(q => `<li>${escapeHtml(q)}</li>`).join('');
        } else {
            qList.innerHTML = '<li class="no-data">Brak pytań — uruchom synchronizację z analizą LLM.</li>';
        }
    }

    // CAPEX renders once, inside the Due Diligence audit block (renderLandAuditHtml).

    // Price adjustment factors
    const negSection = document.getElementById('aiNegotiationSection');
    const negContent = document.getElementById('aiNegotiationContent');
    const btnCopyNeg = document.getElementById('btnCopyNegArgs');

    const leverage = item.negotiation_leverage || 'ŚREDNIA';
    const levMeta = {
        'WYSOKA': { cls: 'positive', label: 'Wysoka' },
        'ŚREDNIA': { cls: 'unknown', label: 'Średnia' },
        'NISKA': { cls: 'negative', label: 'Niska' }
    };
    const lev = levMeta[leverage] || levMeta['ŚREDNIA'];

    const medM2 = item.market_median_m2 ? Math.round(item.market_median_m2).toLocaleString('pl-PL') + ' zł/m²' : 'Brak danych';
    const devText = (item.price_deviation_pct !== null && item.price_deviation_pct !== undefined)
        ? (item.price_deviation_pct > 0 ? `+${item.price_deviation_pct}%` : `${item.price_deviation_pct}%`)
        : '—';

    const fmvText = item.fair_market_value ? Math.round(item.fair_market_value).toLocaleString('pl-PL') + ' zł' : '—';
    const openOfferText = item.suggested_opening_offer ? Math.round(item.suggested_opening_offer).toLocaleString('pl-PL') + ' zł' : '—';

    let diffText = '';
    if (item.price && item.suggested_opening_offer && item.price > item.suggested_opening_offer) {
        const diff = Math.round(item.price - item.suggested_opening_offer);
        const diffPct = Math.round((diff / item.price) * 100);
        diffText = `Rabat: −${diff.toLocaleString('pl-PL')} zł (−${diffPct}%)`;
    }

    const daysOnMkt = item.days_on_market ? `${item.days_on_market} dni` : '—';

    let relistBanner = '';
    if (item.relist_count && item.relist_count > 0) {
        const initP = item.initial_price ? `${Math.round(item.initial_price).toLocaleString('pl-PL')} zł` : null;
        const dropTotal = (initP && item.initial_price > item.price)
            ? `Łączny spadek: <strong>−${Math.round(item.initial_price - item.price).toLocaleString('pl-PL')} zł</strong>`
            : '';
        relistBanner = `
            <div class="relist-banner" style="background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                <div>
                    <span style="font-weight: 700; color: #ef4444;">🔁 WYKRYTO POZORNY RE-LISTING (${item.relist_count}x)</span>
                    <div style="font-size: 12px; color: var(--text-muted); margin-top: 2px;">
                        Pierwotna cena: <strong>${initP || '—'}</strong> ${dropTotal ? ' | ' + dropTotal : ''} | Łączny czas na rynku: <strong>${daysOnMkt}</strong>
                    </div>
                </div>
                <span style="font-size: 11px; padding: 2px 8px; border-radius: 4px; background: rgba(239,68,68,0.2); color: #ef4444; font-weight: 600;">SPRZEDAJĄCY POD PRESJĄ</span>
            </div>
        `;
    }

    const args = item.negotiation_arguments || [];
    let argsHtml = '';
    if (args.length > 0) {
        argsHtml = `
            <div class="nego-args">
                <div class="nego-args-head">Argumenty korygujące cenę</div>
                <ul>
                    ${args.map(a => `<li>${escapeHtml(a)}</li>`).join('')}
                </ul>
            </div>
        `;
        if (btnCopyNeg) btnCopyNeg.style.display = 'inline-flex';
    } else {
        if (btnCopyNeg) btnCopyNeg.style.display = 'none';
    }

    negContent.innerHTML = `
        ${relistBanner}
        <div class="nego-grid">
            <div class="nego-tile">
                <span class="nego-tile-lbl">Pozycja negocjacyjna</span>
                <span class="nego-tile-val">${lev.label}</span>
                <span class="nego-tile-sub">Na rynku: ${daysOnMkt}</span>
            </div>
            <div class="nego-tile">
                <span class="nego-tile-lbl">Mediana rynku</span>
                <span class="nego-tile-val num">${medM2}</span>
                <span class="nego-tile-sub">Odchylenie: <strong>${devText}</strong></span>
            </div>
            <div class="nego-tile">
                <span class="nego-tile-lbl">Wartość godziwa (FMV)</span>
                <span class="nego-tile-val num">${fmvText}</span>
                <span class="nego-tile-sub">Korygowana o stan i wady</span>
            </div>
            <div class="nego-tile">
                <span class="nego-tile-lbl">Oferta otwarcia</span>
                <span class="nego-tile-val num" style="color: var(--green-text);">${openOfferText}</span>
                <span class="nego-tile-sub">${diffText || 'Zgodna z wyceną'}</span>
            </div>
        </div>
        ${argsHtml}
    `;

    // Price corrections history
    const phSection = document.getElementById('aiPriceDropSection');
    const phCount = item.price_history_count || 0;
    if (phCount >= 2) {
        phSection.style.display = 'flex';
        const badgeEl = document.getElementById('aiPriceDropBadge');
        if (item.price_drop_amount) {
            badgeEl.innerHTML = `<span class="ph-badge num">Korekta: −${item.price_drop_amount.toLocaleString('pl-PL')} zł (−${item.price_drop_pct}%)</span>`;
        } else {
            badgeEl.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">Cena bez zmian od pierwszego wpisu.</span>';
        }
        const timeline = document.getElementById('aiPriceTimeline');
        timeline.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">Ładowanie historii cen…</span>';
        Transport.priceHistory(item.id)
            .then(ph => {
                if (!Array.isArray(ph) || ph.length < 2) {
                    phSection.style.display = 'none';
                    return;
                }
                timeline.innerHTML = ph.map(h => {
                    const d = h.date ? new Date(h.date).toLocaleDateString('pl-PL') : '—';
                    return `<div class="ph-entry"><span>${d}</span><span class="ph-price num">${Math.round(h.price).toLocaleString('pl-PL')} zł</span></div>`;
                }).join('');
            })
            .catch(() => {
                timeline.innerHTML = '<span style="font-size:12px;color:var(--text-muted);">Nie udało się pobrać historii cen.</span>';
            });
    } else {
        phSection.style.display = 'none';
    }

    renderAirQualityDrawer(item);

    document.getElementById('aiModal').classList.add('open');
}

function closeAiModal(e) {
    if (e && e.target && e.target.id !== 'aiModal') return;
    document.getElementById('aiModal').classList.remove('open');
    const closedId = currentAiItem ? currentAiItem.id : null;
    currentAiItem = null;
    if (closedId) {
        const card = document.getElementById('card-' + closedId);
        if (card) {
            card.scrollIntoView({ behavior: 'auto', block: 'nearest' });
        }
    }
}

function renderAirQualityDrawer(item) {
    const sec = document.getElementById('aiAirQualitySection');
    const badge = document.getElementById('aiAirQualityBadge');
    const content = document.getElementById('aiAirQualityContent');
    if (!sec || !content) return;

    if (!item || (!item.latitude && !item.longitude && item.air_aqi === null && item.air_pm25_heating_avg === null)) {
        sec.style.display = 'none';
        return;
    }

    sec.style.display = 'flex';

    const risk = item.air_smog_risk || 'NIEZNANE';
    if (risk === 'WYSOKIE') {
        badge.className = 'meta-tag tag-aqi-danger';
        badge.innerText = 'Ryzyko smogu: Wysokie';
    } else if (risk === 'SREDNIE') {
        badge.className = 'meta-tag tag-aqi-warn';
        badge.innerText = 'Ryzyko smogu: Umiarkowane';
    } else if (risk === 'NISKIE') {
        badge.className = 'meta-tag tag-aqi-good';
        badge.innerText = 'Ryzyko smogu: Niskie';
    } else {
        badge.className = 'meta-tag tag-profile';
        badge.innerText = 'CAMS + GIOŚ';
    }

    const aqiVal = (item.air_aqi !== null && item.air_aqi !== undefined) ? `AQI ${item.air_aqi}` : '—';
    const aqiSub = item.air_aqi_label || 'Indeks CAMS';
    const heatVal = (item.air_pm25_heating_avg !== null && item.air_pm25_heating_avg !== undefined) ? `${item.air_pm25_heating_avg} µg/m³` : '—';
    const summerVal = (item.air_pm25_summer_avg !== null && item.air_pm25_summer_avg !== undefined) ? `${item.air_pm25_summer_avg} µg/m³` : '—';
    const smogDaysVal = (item.air_smog_days !== null && item.air_smog_days !== undefined) ? `${item.air_smog_days} dni/rok` : '—';
    const giosStation = item.air_gios_station ? escapeHtml(item.air_gios_station) : 'Brak stacji w pobliżu';
    const giosSub = [
        item.air_gios_dist_km ? `~${item.air_gios_dist_km} km` : '',
        item.air_gios_index ? `Stan: ${escapeHtml(item.air_gios_index)}` : ''
    ].filter(Boolean).join(' · ') || 'Państwowy Monitoring Środowiska';

    content.innerHTML = `
        <div class="aq-section-wrap">
            <div class="aq-tiles-grid">
                <div class="aq-tile">
                    <span class="aq-tile-lbl">Indeks europejski AQI</span>
                    <span class="aq-tile-val num">${aqiVal}</span>
                    <span class="aq-tile-sub">${escapeHtml(aqiSub)}</span>
                </div>
                <div class="aq-tile">
                    <span class="aq-tile-lbl">Średnia PM2.5 (Zima)</span>
                    <span class="aq-tile-val num">${heatVal}</span>
                    <span class="aq-tile-sub">Sezon grzewczy (X–III)</span>
                </div>
                <div class="aq-tile">
                    <span class="aq-tile-lbl">Średnia PM2.5 (Lato)</span>
                    <span class="aq-tile-val num">${summerVal}</span>
                    <span class="aq-tile-sub">Sezon letni (IV–IX)</span>
                </div>
                <div class="aq-tile">
                    <span class="aq-tile-lbl">Dni smogowe</span>
                    <span class="aq-tile-val num">${smogDaysVal}</span>
                    <span class="aq-tile-sub">PM2.5 > 25 µg/m³ (WHO)</span>
                </div>
                <div class="aq-tile">
                    <span class="aq-tile-lbl">Stacja GIOŚ</span>
                    <span class="aq-tile-val" style="font-size:12px;" title="${giosStation}">${giosStation}</span>
                    <span class="aq-tile-sub">${giosSub}</span>
                </div>
            </div>

            <div class="aq-chart-container">
                <div class="aq-chart-head">
                    <span class="aq-chart-title">Sezonowy profil stężenia PM2.5 (ostatnie 12 miesięcy)</span>
                    <span class="aq-chart-unit">µg/m³ (norma WHO: 15 µg/m³)</span>
                </div>
                <div class="aq-bars-flex" id="aqBarsContainer">
                    <div style="width:100%;text-align:center;padding:30px 0;color:var(--text-muted);font-size:11px;">Ładowanie profilu 12-miesięcznego…</div>
                </div>
                <div class="aq-chart-legend">
                    <div class="aq-legend-item"><span class="aq-legend-swatch" style="background:#22c55e;"></span> Do 15 µg/m³ (Norma WHO)</div>
                    <div class="aq-legend-item"><span class="aq-legend-swatch" style="background:#f59e0b;"></span> 15–25 µg/m³ (Umiarkowane)</div>
                    <div class="aq-legend-item"><span class="aq-legend-swatch" style="background:#ef4444;"></span> > 25 µg/m³ (Smog)</div>
                    <div class="aq-guide-line-hint">Pogrubione etykiety = sezon grzewczy</div>
                </div>
            </div>
        </div>
    `;

    if (item.id) {
        Transport.airQuality(item.id)
            .then(data => {
                const barsContainer = document.getElementById('aqBarsContainer');
                if (!barsContainer) return;
                const monthly = data.monthly_averages || [];
                if (!monthly.length) {
                    barsContainer.innerHTML = '<div style="width:100%;text-align:center;padding:25px 0;color:var(--text-muted);font-size:11px;">Brak szczegółowych danych CAMS dla tej lokalizacji.</div>';
                    return;
                }
                const maxVal = Math.max(35, ...monthly.map(m => m.pm2_5 || 0));
                barsContainer.innerHTML = monthly.map(m => {
                    const p25 = m.pm2_5 || 0;
                    const heightPct = Math.min(100, Math.max(5, Math.round((p25 / maxVal) * 100)));
                    let color = '#22c55e';
                    if (p25 > 25.0) color = '#ef4444';
                    else if (p25 > 15.0) color = '#f59e0b';

                    const winterCls = m.is_heating_season ? 'is-winter' : '';
                    return `
                        <div class="aq-bar-group ${winterCls}">
                            <span class="aq-bar-val-text">${p25.toFixed(1)}</span>
                            <div class="aq-bar-pillar" style="height:${heightPct}%; background:${color};" title="${escapeHtml(m.month_name)}: PM2.5 ${p25.toFixed(1)} µg/m³, PM10 ${(m.pm10 || 0).toFixed(1)} µg/m³, dni smogowe: ${m.smog_days}"></div>
                            <span class="aq-bar-month-lbl">${escapeHtml(m.month_name)}</span>
                        </div>
                    `;
                }).join('');
            })
            .catch(() => {
                const barsContainer = document.getElementById('aqBarsContainer');
                if (barsContainer) {
                    barsContainer.innerHTML = '<div style="width:100%;text-align:center;padding:25px 0;color:var(--text-muted);font-size:11px;">Nie udało się pobrać szczegółowych danych jakości powietrza.</div>';
                }
            });
    }
}

function copyAiQuestions() {
    if (!currentAiItem) return;
    const sq = currentAiItem.stakeholder_questions;
    const roleTitles = {
        'seller': 'Pytania do sprzedającego / pośrednika',
        'community': 'Pytania do zarządcy / wspólnoty',
        'notary': 'Pytania do kancelarii notarialnej',
        'municipality': 'Pytania do wydziału architektury / urzędu gminy'
    };
    let text = '';
    if (sq && typeof sq === 'object' && Object.keys(sq).length > 0) {
        for (const [role, list] of Object.entries(sq)) {
            if (Array.isArray(list) && list.length > 0) {
                text += `\n[${roleTitles[role] || role}]\n`;
                text += list.map((q, i) => `${i + 1}. ${q}`).join('\n') + '\n';
            }
        }
    }
    if (!text.trim()) {
        const qs = currentAiItem.ai_questions || [];
        if (qs.length === 0) { showToast('Brak pytań do skopiowania.'); return; }
        text = qs.map((q, i) => `${i + 1}. ${q}`).join('\n');
    }
    navigator.clipboard.writeText(text.trim()).then(() => showToast('Pytania skopiowane do schowka.'));
}

function copyAiDocuments() {
    if (!currentAiItem) return;
    const docs = currentAiItem.documents_to_obtain || [];
    if (docs.length === 0) { showToast('Brak dokumentów do skopiowania.'); return; }
    const text = 'Dokumenty do weryfikacji przed transakcją:\n' + docs.map((d, i) => `${i + 1}. [ ] ${d}`).join('\n');
    navigator.clipboard.writeText(text).then(() => showToast('Checklista dokumentów skopiowana do schowka.'));
}

function copyAiSms() {
    if (!currentAiItem) return;
    const item = currentAiItem;
    const title = item.title || '';
    const price = item.price ? Math.round(item.price).toLocaleString('pl-PL') + ' zł' : '';
    const sms = `Dzień dobry,\nJestem zainteresowany/a ofertą: "${title}" (${price}).\nCzy nieruchomość jest nadal dostępna? Kiedy mogę umówić się na oględziny?\nPozdrawiam`;
    navigator.clipboard.writeText(sms).then(() => showToast('Gotowa wiadomość SMS skopiowana.'));
}

function copyNegotiationArguments() {
    if (!currentAiItem) return;
    const args = currentAiItem.negotiation_arguments || [];
    if (args.length === 0) { showToast('Brak argumentów do skopiowania.'); return; }
    const heading = `Strategia negocjacyjna dla oferty: ${currentAiItem.title} (${currentAiItem.url})\n` +
        `Sugerowane otwarcie: ${currentAiItem.suggested_opening_offer ? Math.round(currentAiItem.suggested_opening_offer).toLocaleString('pl-PL') + ' zł' : 'b/d'}\n\n` +
        `Argumenty korygujące cenę:\n`;
    const text = heading + args.map((a, i) => `${i + 1}. ${a}`).join('\n');
    navigator.clipboard.writeText(text).then(() => showToast('Argumenty negocjacyjne skopiowane do schowka.'));
}

async function triggerAiAuditForCurrentItem() {
    if (!currentAiItem) return;
    const item = currentAiItem;
    const btn = document.getElementById('btnGenerateAiAudit');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-inline"></span> Generowanie…';
    }
    const summaryEl = document.getElementById('aiSummaryContent');
    if (summaryEl) {
        summaryEl.innerHTML = '<div style="display:flex;align-items:center;gap:8px;color:var(--text-muted);padding:10px 0;"><span class="spinner-inline"></span> Trwa weryfikacja techniczna i prawna opisu przez AI…</div>';
    }
    try {
        const resp = await fetch(`/api/listings/${item.id}/ai-audit`, { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.error || `Błąd serwera (${resp.status})`);
        }
        const data = await resp.json();
        item._detailLoaded = false;
        Object.assign(item, data);
        const inList = allListings.find(i => i.id === item.id);
        if (inList) {
            inList._detailLoaded = false;
            Object.assign(inList, data);
        }
        await openAiModal(item.id, true);
        showToast('Raport AI został pomyślnie wygenerowany!');
    } catch (err) {
        showToast('Błąd generowania raportu AI: ' + err.message);
        if (summaryEl) {
            summaryEl.innerHTML = `
                <div style="display:flex;flex-direction:column;gap:8px;padding:10px 12px;background:var(--surface-2);border-radius:var(--r-md);border:1px dashed var(--border);">
                    <span style="color:var(--red-text);font-size:var(--font-size-xs);">Nie udało się wygenerować raportu: ${escapeHtml(err.message)}</span>
                    <button class="btn btn-sm btn-ai-audit" style="align-self:flex-start;" onclick="triggerAiAuditForCurrentItem()">
                        🔄 Ponów próbę
                    </button>
                </div>
            `;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = item.ai_summary ? '🔄 Odśwież raport AI' : '🤖 Generuj raport AI';
        }
    }
}

function recalcMortgage() {
    const priceInput = document.getElementById('mortgageBasePrice');
    const ownInput = document.getElementById('mortgageOwnPct');
    const yearsInput = document.getElementById('mortgageYears');
    const rateInput = document.getElementById('mortgageRate');
    const monthlyEl = document.getElementById('mortgageMonthlyPay');
    const loanEl = document.getElementById('mortgageLoanAmount');
    const interestEl = document.getElementById('mortgageTotalInterest');

    if (!priceInput || !monthlyEl) return;

    const price = Math.max(0, parseFloat(priceInput.value) || 0);
    const ownPct = Math.min(95, Math.max(0, parseFloat(ownInput ? ownInput.value : 20) || 20));
    const years = Math.max(1, parseInt(yearsInput ? yearsInput.value : 25, 10) || 25);
    const annualRate = Math.max(0.1, parseFloat(rateInput ? rateInput.value : 7.2) || 7.2);

    const loanAmount = Math.max(0, price * (1 - ownPct / 100));
    const monthlyRate = (annualRate / 100) / 12;
    const totalMonths = years * 12;

    let monthlyPayment = 0;
    if (loanAmount > 0) {
        if (monthlyRate > 0) {
            monthlyPayment = loanAmount * (monthlyRate * Math.pow(1 + monthlyRate, totalMonths)) / (Math.pow(1 + monthlyRate, totalMonths) - 1);
        } else {
            monthlyPayment = loanAmount / totalMonths;
        }
    }

    const totalRepay = monthlyPayment * totalMonths;
    const totalInterest = Math.max(0, totalRepay - loanAmount);

    monthlyEl.textContent = `${Math.round(monthlyPayment).toLocaleString('pl-PL')} zł / mc`;
    if (loanEl) loanEl.textContent = `${Math.round(loanAmount).toLocaleString('pl-PL')} zł (wkład: ${Math.round(price * (ownPct / 100)).toLocaleString('pl-PL')} zł)`;
    if (interestEl) interestEl.textContent = `+${Math.round(totalInterest).toLocaleString('pl-PL')} zł (${Math.round((totalInterest / (loanAmount || 1)) * 100)}%)`;
}
window.recalcMortgage = recalcMortgage;
