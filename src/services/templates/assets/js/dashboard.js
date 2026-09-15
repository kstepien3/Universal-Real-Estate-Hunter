        let allListings = [];
        let currentFilter = 'ALL';
        let currentViewMode = 'split';
        let map = null;
        let markersGroup = null;
        let markersMap = {};
        let aqiLayer = null;
        let aqiLayerActive = false;
        let coordCounts = {};
        let activeConfig = null;
        let fetchEtag = null;
        let lastServerSigs = {};
        let lastFetchAt = 0;
        let searchDebounceTimer = null;
        let gridRenderToken = 0;

        function showToast(msg) {
            const t = document.getElementById('toast');
            if (!t) return;
            t.innerText = msg;
            t.style.display = 'block';
            setTimeout(() => { t.style.display = 'none'; }, 3200);
        }

        // ========================
        // Map (Leaflet + OpenStreetMap dark tiles)
        // ========================
        function initLeafletMap(centerCoords = [52.0693, 19.4803]) {
            if (map) {
                map.setView(centerCoords, 12);
                return;
            }

            map = L.map('map', {
                center: centerCoords,
                zoom: 12,
                zoomControl: true,
                scrollWheelZoom: true,
            });

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
                maxZoom: 19
            }).addTo(map);

            markersGroup = L.featureGroup().addTo(map);
        }

        function ensureMapInitialized() {
            const mw = document.getElementById('mapWrapper');
            if (mw && mw.offsetWidth === 0 && mw.offsetHeight === 0) return;
            if (map) {
                setTimeout(() => map.invalidateSize(), 150);
                return;
            }
            const curProf = allProfiles.find(p => p.id === selectedProfileId) || allProfiles[0];
            const center = curProf ? getCityCenter(curProf.city) : [52.0693, 19.4803];
            initLeafletMap(center);
            renderMapMarkers(computeFilteredItems());
            setTimeout(() => map.invalidateSize(), 200);
        }

        function syncTabletToggle() {
            const tglList = document.getElementById('tglList');
            const tglMap = document.getElementById('tglMap');
            if (!tglList || !tglMap) return;
            const mapShown = document.body.classList.contains('tablet-pane-map');
            tglList.classList.toggle('active', !mapShown);
            tglMap.classList.toggle('active', mapShown);
        }

        function switchViewMode(mode) {
            currentViewMode = mode;
            document.body.classList.remove('mode-grid', 'mode-split', 'mode-map');
            document.body.classList.add('mode-' + mode);
            document.body.classList.remove('mobile-map-open');
            if (mode === 'map') {
                document.body.classList.remove('tablet-pane-list');
                document.body.classList.add('tablet-pane-map');
            } else {
                document.body.classList.add('tablet-pane-list');
                document.body.classList.remove('tablet-pane-map');
            }

            document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
            if (mode === 'grid') document.getElementById('btnViewGrid')?.classList.add('active');
            if (mode === 'split') document.getElementById('btnViewSplit')?.classList.add('active');
            if (mode === 'map') document.getElementById('btnViewMap')?.classList.add('active');

            syncTabletToggle();
            syncMapFab();
            ensureMapInitialized();

            setTimeout(() => {
                if (map) map.invalidateSize();
            }, 250);
        }

        function tabletShow(pane) {
            document.body.classList.toggle('tablet-pane-map', pane === 'map');
            document.body.classList.toggle('tablet-pane-list', pane === 'list');
            syncTabletToggle();
            if (pane === 'map') ensureMapInitialized();
            setTimeout(() => {
                if (map) map.invalidateSize();
            }, 150);
        }

        function toggleMobileMap() {
            const open = document.body.classList.toggle('mobile-map-open');
            syncMapFab();
            const fab = document.getElementById('mapFab');
            if (fab) {
                fab.setAttribute('aria-label', open ? 'Pokaż listę ofert' : 'Pokaż pełnoekranową mapę');
                fab.setAttribute('aria-pressed', open ? 'true' : 'false');
            }
            if (open) ensureMapInitialized();
            setTimeout(() => {
                if (map) map.invalidateSize();
            }, 250);
        }

        function syncMapFab() {
            const open = document.body.classList.contains('mobile-map-open');
            const iconMap = document.getElementById('mapFabIconMap');
            const iconClose = document.getElementById('mapFabIconClose');
            if (iconMap) iconMap.style.display = open ? 'none' : '';
            if (iconClose) iconClose.style.display = open ? '' : 'none';
            const fab = document.getElementById('mapFab');
            if (fab) {
                fab.setAttribute('aria-label', open ? 'Pokaż listę ofert' : 'Pokaż pełnoekranową mapę');
                fab.setAttribute('aria-pressed', open ? 'true' : 'false');
            }
        }

        // ========================
        // Profiles
        // ========================
        let allProfiles = [];
        let currentProfileId = null;
        let selectedProfileId = null;
        let scrapersConfig = null;

        const CITY_CENTROIDS_JS = {
            "rzeszow": [50.0375, 22.0047],
            "krakow": [50.0647, 19.9450],
            "warszawa": [52.2297, 21.0122],
            "wroclaw": [51.1079, 17.0385],
            "lublin": [51.2465, 22.5684],
            "poznan": [52.4064, 16.9252],
            "gdansk": [54.3520, 18.6466],
            "gdynia": [54.5189, 18.5305],
            "sopot": [54.4418, 18.5600],
            "katowice": [50.2649, 19.0238],
            "lodz": [51.7592, 19.4560],
            "szczecin": [53.4285, 14.5528],
            "bialystok": [53.1325, 23.1688],
            "kielce": [50.8661, 20.6286],
            "bydgoszcz": [53.1235, 18.0084],
            "torun": [53.0138, 18.5984],
            "radom": [51.4027, 21.1471],
            "czestochowa": [50.8118, 19.1203],
            "sosnowiec": [50.2863, 19.1041],
            "gliwice": [50.2945, 18.6714],
            "zabrze": [50.3249, 18.7857],
            "olsztyn": [53.7784, 20.4801],
            "bielskobiala": [49.8224, 19.0444],
            "zielonagora": [51.9356, 15.5062],
            "siedlce": [52.1677, 22.2901],
            "tarnow": [50.0121, 20.9858],
            "krosno": [49.6887, 21.7706],
            "przemysl": [49.7839, 22.7678],
            "mielec": [50.2872, 21.4239],
            "lancut": [50.0690, 22.2310]
        };

        function getCityCenter(cityName) {
            if (!cityName) return [52.0693, 19.4803];
            const clean = cityName.toLowerCase()
                .replace(/ą/g,'a').replace(/ć/g,'c').replace(/ę/g,'e').replace(/ł/g,'l').replace(/ń/g,'n')
                .replace(/ó/g,'o').replace(/ś/g,'s').replace(/ź/g,'z').replace(/ż/g,'z')
                .replace(/[^a-z0-9]+/g, '');
            return CITY_CENTROIDS_JS[clean] || [52.0693, 19.4803];
        }

        function toggleProfileMenu(event) {
            if (event) event.stopPropagation();
            const menu = document.getElementById('profileTabsContainer');
            if (menu) menu.classList.toggle('open');
        }

        function closeProfileMenu() {
            const menu = document.getElementById('profileTabsContainer');
            if (menu) menu.classList.remove('open');
        }

        function categoryIconSvg(cat, size = 14) {
            const stroke = 'stroke="currentColor" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"';
            if (cat === 'mieszkanie') {
                return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" ${stroke}><rect x="4" y="2" width="16" height="20" rx="1"></rect><path d="M9 22v-4h6v4"></path><path d="M8 6h.01"></path><path d="M16 6h.01"></path><path d="M12 6h.01"></path><path d="M12 10h.01"></path><path d="M12 14h.01"></path><path d="M16 10h.01"></path><path d="M16 14h.01"></path><path d="M8 10h.01"></path><path d="M8 14h.01"></path></svg>`;
            }
            if (cat === 'dzialka') {
                return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" ${stroke}><path d="M2 22 22 2"></path><path d="M11 22h7a4 4 0 0 0 4-4v-7"></path><path d="M2 13a9 9 0 0 0 18 0"></path></svg>`;
            }
            return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" ${stroke}><path d="M3 21h18"></path><path d="M5 21V7l8-4v18"></path><path d="M19 21V11l-6-4"></path><path d="M9 9v.01"></path><path d="M9 12v.01"></path><path d="M9 15v.01"></path><path d="M9 18v.01"></path></svg>`;
        }

        function switchActiveProfile(profileId) {
            selectedProfileId = profileId;
            try {
                localStorage.setItem('hunter_selected_profile_id', profileId);
            } catch(e) {}

            if (profileId === 'ALL') {
                const labelEl = document.getElementById('profileDropLabel');
                if (labelEl) labelEl.innerText = `Wszystkie profile (${allProfiles.length})`;
                const countEl = document.getElementById('profileDropCount');
                if (countEl) countEl.innerText = allListings.length;
            } else {
                const prof = allProfiles.find(p => p.id === profileId) || allProfiles[0];
                if (prof) {
                    const labelEl = document.getElementById('profileDropLabel');
                    if (labelEl) labelEl.innerText = `${prof.name} (${prof.city} +${prof.distance_radius} km)`;
                    const countEl = document.getElementById('profileDropCount');
                    if (countEl) countEl.innerText = getListingsForActiveProfile().length;

                    if (map) {
                        const profItemsWithCoords = getListingsForActiveProfile().filter(i => i.latitude && i.longitude);
                        if (profItemsWithCoords.length === 0 && prof.city) {
                            map.setView(getCityCenter(prof.city), 12);
                        }
                    }
                }
            }

            const pSelect = document.getElementById('filterProfile');
            if (pSelect) {
                pSelect.value = profileId;
            }

            closeProfileMenu();
            renderProfileTabs();
            applyFilters();
            if (profileId === 'ALL') {
                showToast('Widok: Wszystkie profile');
            } else {
                const prof = allProfiles.find(p => p.id === profileId);
                if (prof) {
                    showToast(`Widok profilu: ${prof.name}`);
                }
            }
        }

        function renderProfileTabs() {
            const container = document.getElementById('profileTabsContainer');
            if (!container) return;

            if (allProfiles.length === 0) {
                container.innerHTML = '<div style="color:var(--text-muted);font-size:11px;padding:8px 10px;">Brak profili</div>';
                return;
            }

            if (!selectedProfileId || (selectedProfileId !== 'ALL' && !allProfiles.some(p => p.id === selectedProfileId))) {
                try {
                    const saved = localStorage.getItem('hunter_selected_profile_id');
                    if (saved && (saved === 'ALL' || allProfiles.some(p => p.id === saved))) {
                        selectedProfileId = saved;
                    } else {
                        selectedProfileId = allProfiles[0].id;
                    }
                } catch(e) {
                    selectedProfileId = allProfiles[0].id;
                }
            }

            const allCount = allListings.length;
            const allIsActive = selectedProfileId === 'ALL';
            const allItem = `
                <button type="button" class="profile-menu-item ${allIsActive ? 'active' : ''}" onclick="switchActiveProfile('ALL')" title="Wszystkie profile">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="3" width="7" height="7" rx="1"></rect>
                        <rect x="14" y="3" width="7" height="7" rx="1"></rect>
                        <rect x="14" y="14" width="7" height="7" rx="1"></rect>
                        <rect x="3" y="14" width="7" height="7" rx="1"></rect>
                    </svg>
                    <span class="profile-menu-item-meta">
                        <span class="profile-menu-item-name">Wszystkie profile</span>
                        <span class="profile-menu-item-sub">Cała baza ofert</span>
                    </span>
                    <span class="profile-drop-count num">${allCount}</span>
                </button>
            `;

            const items = allProfiles.map(p => {
                const isActive = selectedProfileId === p.id;
                const pCity = (p.city || '').toLowerCase();
                const count = allListings.filter(item => {
                    if (item.profile_id && item.profile_id === p.id) return true;
                    if (item.profile_name && item.profile_name === p.name) return true;
                    if (!item.profile_id || item.profile_id === 'default') {
                        if (!pCity || (item.city || '').toLowerCase() === pCity) return true;
                    }
                    return false;
                }).length;

                return `
                    <button type="button" class="profile-menu-item ${isActive ? 'active' : ''}" onclick="switchActiveProfile('${escapeHtml(p.id)}')" title="${escapeHtml(p.name)} (${escapeHtml(p.city)})">
                        ${categoryIconSvg(p.category || 'dom')}
                        <span class="profile-menu-item-meta">
                            <span class="profile-menu-item-name">${escapeHtml(p.name)}</span>
                            <span class="profile-menu-item-sub">${escapeHtml(p.city)} · +${p.distance_radius ?? 15} km</span>
                        </span>
                        <span class="profile-drop-count num">${count}</span>
                    </button>
                `;
            }).join('');

            const footer = `
                <div class="profile-menu-footer">
                    <button type="button" class="profile-menu-item" onclick="openConfigModal(); switchConfigTab('profiles');">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                        </svg>
                        Zarządzaj profilami
                    </button>
                </div>
            `;

            container.innerHTML = allItem + items + footer;
        }

        function getListingsForActiveProfile() {
            if (selectedProfileId === 'ALL') {
                return allListings;
            }

            if (!selectedProfileId) {
                if (allProfiles.length > 0) {
                    selectedProfileId = allProfiles[0].id;
                } else {
                    return allListings;
                }
            }

            const prof = allProfiles.find(p => p.id === selectedProfileId);
            if (!prof) {
                return allListings;
            }

            const profName = prof.name;
            const profCity = (prof.city || '').toLowerCase();

            return allListings.filter(item => {
                if (item.profile_id && item.profile_id === selectedProfileId) return true;
                if (profName && item.profile_name === profName) return true;
                if (!item.profile_id || item.profile_id === 'default') {
                    if (!profCity || (item.city || '').toLowerCase() === profCity) {
                        return true;
                    }
                }
                return false;
            });
        }

        // ========================
        // Configuration modal
        // ========================
        function switchConfigTab(tab) {
            const tabs = [
                ['tabBtnProfiles', 'configTabProfiles', 'profiles'],
                ['tabBtnCapex', 'configTabCapex', 'capex'],
                ['tabBtnScrapers', 'configTabScrapers', 'scrapers'],
                ['tabBtnScheduler', 'configTabScheduler', 'scheduler'],
                ['tabBtnAi', 'configTabAi', 'ai'],
            ];
            tabs.forEach(([btnId, tabId, name]) => {
                const btn = document.getElementById(btnId);
                if (btn) {
                    btn.classList.toggle('active', tab === name);
                    if (tab === name) {
                        btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                    }
                }
                const tabEl = document.getElementById(tabId);
                if (tabEl) tabEl.style.display = (tab === name) ? 'block' : 'none';
            });
            if (tab === 'ai' && typeof checkLlmStatusIfEmpty === 'function') {
                checkLlmStatusIfEmpty();
            }
        }

        function toggleSchedulerInputs(enabled) {
            const day = document.getElementById('dayIntervalField');
            const nightRow = document.getElementById('cfgNightMode')?.closest('div');
            const row = document.getElementById('nightSettingsRow');
            const interval = document.getElementById('nightIntervalField');
            [day, nightRow, row, interval].forEach(el => {
                if (!el) return;
                el.style.opacity = enabled ? '1' : '0.4';
                el.style.pointerEvents = enabled ? 'auto' : 'none';
            });
        }

        function toggleNightModeInputs(enabled) {
            const row = document.getElementById('nightSettingsRow');
            const interval = document.getElementById('nightIntervalField');
            if (row) {
                row.style.opacity = enabled ? '1' : '0.4';
                row.style.pointerEvents = enabled ? 'auto' : 'none';
            }
            if (interval) {
                interval.style.opacity = enabled ? '1' : '0.4';
                interval.style.pointerEvents = enabled ? 'auto' : 'none';
            }
        }

        function onCategorySelectChange(cat) {
            const bDom = document.getElementById('catBoxDom');
            const bApt = document.getElementById('catBoxMieszkanie');
            const bPlot = document.getElementById('catBoxDzialka');
            if (bDom) bDom.style.display = (cat === 'dom') ? 'block' : 'none';
            if (bApt) bApt.style.display = (cat === 'mieszkanie') ? 'block' : 'none';
            if (bPlot) bPlot.style.display = (cat === 'dzialka') ? 'block' : 'none';
        }

        async function fetchConfig() {
            try {
                activeConfig = await Transport.fetchConfig();
                allProfiles = activeConfig.profiles || [];
                scrapersConfig = activeConfig.scrapers || {
                    otodom: { enabled: true, max_pages: 5 },
                    olx: { enabled: true, max_pages: 5 },
                    nieruchomosci_online: { enabled: true, max_pages: 5 },
                    morizon: { enabled: true, max_pages: 5 },
                    request_delay: 1.0
                };
                if (allProfiles.length === 0) {
                    allProfiles = [{
                        id: "default",
                        name: "Domy Rzeszów",
                        category: "dom",
                        enabled: true,
                        city: activeConfig.city || "Rzeszów",
                        distance_radius: activeConfig.distance_radius || 15,
                        market_type: activeConfig.market_type || "all",
                        owner_type: "all",
                        min_price: activeConfig.min_price || 0,
                        max_price: activeConfig.max_price || 1300000,
                        min_area_home: activeConfig.min_area_home || 90,
                        max_area_home: activeConfig.max_area_home || 145,
                        min_area_plot: activeConfig.min_area_plot || 250,
                        blacklist_keywords: activeConfig.blacklist_keywords || []
                    }];
                }

                if (!selectedProfileId || !allProfiles.some(p => p.id === selectedProfileId)) {
                    try {
                        const saved = localStorage.getItem('hunter_selected_profile_id');
                        if (saved && allProfiles.some(p => p.id === saved)) {
                            selectedProfileId = saved;
                        } else {
                            selectedProfileId = allProfiles[0].id;
                        }
                    } catch(e) {
                        selectedProfileId = allProfiles[0].id;
                    }
                }

                const pSelect = document.getElementById('filterProfile');
                if (pSelect) {
                    pSelect.innerHTML = '<option value="ALL">Wszystkie profile</option>' +
                        allProfiles.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)} (${escapeHtml(p.city)})</option>`).join('');
                    pSelect.value = selectedProfileId;
                    pSelect.onchange = function() { switchActiveProfile(this.value); };
                }

                renderProfileTabs();

                const curProf = allProfiles.find(p => p.id === selectedProfileId) || allProfiles[0];
                const labelEl = document.getElementById('profileDropLabel');
                if (labelEl && curProf) {
                    labelEl.innerText = `${curProf.name} (${curProf.city} +${curProf.distance_radius} km)`;
                }
            } catch (err) {
                console.error("Failed to load config:", err);
            }
        }

        function refreshProfileSelect() {
            const sel = document.getElementById('cfgProfileSelect');
            if (!sel) return;
            sel.innerHTML = allProfiles.map(p => {
                const stateDot = p.enabled ? '●' : '○';
                return `<option value="${p.id}">${stateDot} ${escapeHtml(p.name)} (${p.city})</option>`;
            }).join('');
            if (currentProfileId) sel.value = currentProfileId;
        }

        function loadProfileIntoForm(profId) {
            const p = allProfiles.find(x => x.id === profId) || allProfiles[0];
            if (!p) return;
            currentProfileId = p.id;

            document.getElementById('cfgProfileName').value = p.name || '';
            document.getElementById('cfgProfileCategory').value = p.category || 'dom';
            document.getElementById('cfgProfileEnabled').checked = p.enabled !== false;
            document.getElementById('cfgCity').value = p.city || 'Rzeszów';
            document.getElementById('cfgRadius').value = p.distance_radius ?? 15;
            document.getElementById('cfgMarket').value = p.market_type || 'all';
            document.getElementById('cfgOwnerType').value = p.owner_type || 'all';
            document.getElementById('cfgMinPrice').value = p.min_price || 0;
            document.getElementById('cfgMaxPrice').value = p.max_price || 1300000;
            document.getElementById('cfgMinPriceM2').value = p.min_price_per_m2 || '';
            document.getElementById('cfgMaxPriceM2').value = p.max_price_per_m2 || '';
            document.getElementById('cfgMinYearBuilt').value = p.min_year_built || '';
            document.getElementById('cfgMaxYearBuilt').value = p.max_year_built || '';
            document.getElementById('cfgBlacklist').value = (p.blacklist_keywords || []).join(', ');
            document.getElementById('cfgDiscordWebhook').value = p.discord_webhook_url || '';

            const finishAllowed = p.allowed_finish_conditions || [];
            const finishAll = finishAllowed.includes('all');
            document.querySelectorAll('.cfg-finish').forEach(cb => { cb.checked = !finishAll && finishAllowed.includes(cb.value); });
            const buildingAllowed = p.building_types || [];
            document.querySelectorAll('.cfg-building').forEach(cb => { cb.checked = buildingAllowed.includes(cb.value); });
            const heatingAllowed = p.allowed_heating_types || [];
            const heatingAll = heatingAllowed.includes('all');
            document.querySelectorAll('.cfg-heating').forEach(cb => { cb.checked = !heatingAll && heatingAllowed.includes(cb.value); });
            document.getElementById('cfgRejectSeptic').checked = !!p.reject_septic_tank;
            document.getElementById('cfgAllowVis').checked = p.allow_visualisations !== false;
            if (document.getElementById('cfgRejectFlood')) document.getElementById('cfgRejectFlood').checked = !!p.reject_flood_risk;
            if (document.getElementById('cfgRejectLandslide')) document.getElementById('cfgRejectLandslide').checked = !!p.reject_landslide_risk;
            if (document.getElementById('cfgRejectHV')) document.getElementById('cfgRejectHV').checked = !!p.reject_high_voltage;
            if (document.getElementById('cfgMinFront')) document.getElementById('cfgMinFront').value = (p.min_parcel_front_m !== null && p.min_parcel_front_m !== undefined) ? p.min_parcel_front_m : '';

            document.getElementById('cfgMinAreaHome').value = p.min_area_home || '';
            document.getElementById('cfgMaxAreaHome').value = p.max_area_home || '';
            document.getElementById('cfgMinAreaPlot').value = p.min_area_plot || '';
            document.getElementById('cfgMaxAreaPlot').value = p.max_area_plot || '';

            document.getElementById('cfgMinAreaApt').value = p.min_area_home || '';
            document.getElementById('cfgMaxAreaApt').value = p.max_area_home || '';
            document.getElementById('cfgMinRooms').value = p.min_rooms || '';
            document.getElementById('cfgMaxRooms').value = p.max_rooms || '';
            document.getElementById('cfgMinFloor').value = (p.min_floor !== undefined && p.min_floor !== null) ? p.min_floor : '';
            document.getElementById('cfgMaxFloor').value = (p.max_floor !== undefined && p.max_floor !== null) ? p.max_floor : '';

            document.getElementById('cfgMinAreaPlotOnly').value = p.min_area_plot || '';
            document.getElementById('cfgMaxAreaPlotOnly').value = p.max_area_plot || '';

            onCategorySelectChange(p.category || 'dom');
        }

        function onProfileSelectChange(val) {
            saveCurrentFormIntoMemory();
            currentProfileId = val;
            loadProfileIntoForm(val);
        }

        function duplicateCurrentProfile() {
            saveCurrentFormIntoMemory();
            const curP = allProfiles.find(x => x.id === currentProfileId) || allProfiles[0];
            if (!curP) return;

            const newId = "profile_" + Date.now();
            const cloned = JSON.parse(JSON.stringify(curP));
            cloned.id = newId;

            // Generate clean copy name: e.g. "Domy Rzeszów (kopia)"
            let baseName = (curP.name || "Profil").replace(/\s*\(kopia(?:\s+\d+)?\)$/i, '').trim();
            let copyName = `${baseName} (kopia)`;
            let counter = 2;
            while (allProfiles.some(p => (p.name || '').toLowerCase() === copyName.toLowerCase())) {
                copyName = `${baseName} (kopia ${counter})`;
                counter++;
            }
            cloned.name = copyName;

            allProfiles.push(cloned);
            currentProfileId = newId;
            refreshProfileSelect();
            loadProfileIntoForm(newId);
            renderProfileTabs();
            showToast(`Zduplikowano profil "${curP.name}" ➔ "${cloned.name}". Zmień miasto lub parametry i kliknij Zapisz.`);

            const cityInput = document.getElementById('cfgCity');
            if (cityInput) {
                cityInput.focus();
                cityInput.select();
            }
        }

        function createNewProfile() {
            saveCurrentFormIntoMemory();
            const newId = "profile_" + Date.now();
            const newP = {
                id: newId,
                name: "Nowy profil " + (allProfiles.length + 1),
                category: "mieszkanie",
                enabled: true,
                city: "Kraków",
                distance_radius: 10,
                market_type: "all",
                owner_type: "all",
                min_price: 300000,
                max_price: 900000,
                min_area_home: 40,
                max_area_home: 80,
                min_rooms: 2,
                max_rooms: 4,
                blacklist_keywords: [],
                allowed_finish_conditions: ["all"],
                building_types: ["szeregowiec", "bliźniak", "wolnostojący", "inny"],
                allowed_heating_types: ["all"],
                allow_visualisations: true,
                reject_septic_tank: false,
                reject_flood_risk: false,
                reject_landslide_risk: false,
                reject_high_voltage: false,
                min_parcel_front_m: null
            };
            allProfiles.push(newP);
            currentProfileId = newId;
            refreshProfileSelect();
            loadProfileIntoForm(newId);
            showToast("Utworzono nowy profil");
        }

        async function deleteCurrentProfile() {
            if (allProfiles.length <= 1) {
                alert("Nie można usunąć jedynego profilu wyszukiwania.");
                return;
            }
            const profName = document.getElementById('cfgProfileName').value || currentProfileId;
            if (!confirm(`Czy na pewno chcesz usunąć profil "${profName}" oraz WSZYSTKIE powiązane z nim oferty z bazy danych?\n\nTej operacji nie można cofnąć.`)) {
                return;
            }

            const idToDelete = currentProfileId;
            try {
                const resData = await Transport.deleteProfile(idToDelete);
                const deletedListingsCount = resData.deleted_listings || 0;

                const idx = allProfiles.findIndex(p => p.id === idToDelete);
                if (idx >= 0) {
                    allProfiles.splice(idx, 1);
                }

                if (selectedProfileId === idToDelete) {
                    selectedProfileId = allProfiles[0].id;
                }
                currentProfileId = allProfiles[0].id;

                refreshProfileSelect();
                loadProfileIntoForm(currentProfileId);

                showToast(`Usunięto profil "${profName}" oraz ${deletedListingsCount} ofert z bazy danych.`);

                await fetchListings();
                renderProfileTabs();
                switchActiveProfile(currentProfileId);
            } catch (err) {
                console.error("Failed to delete profile:", err);
                showToast("Błąd podczas usuwania profilu.");
            }
        }

        function saveCurrentFormIntoMemory() {
            const p = allProfiles.find(x => x.id === currentProfileId);
            if (!p) return;
            const cat = document.getElementById('cfgProfileCategory').value;
            p.name = document.getElementById('cfgProfileName').value.trim() || p.name;
            p.category = cat;
            p.enabled = document.getElementById('cfgProfileEnabled').checked;
            p.city = document.getElementById('cfgCity').value.trim() || p.city || '';
            p.distance_radius = parseInt(document.getElementById('cfgRadius').value) || 15;
            p.market_type = document.getElementById('cfgMarket').value;
            p.owner_type = document.getElementById('cfgOwnerType').value;
            p.min_price = parseFloat(document.getElementById('cfgMinPrice').value) || 0;
            p.max_price = parseFloat(document.getElementById('cfgMaxPrice').value) || 1300000;
            p.min_price_per_m2 = parseFloat(document.getElementById('cfgMinPriceM2').value) || null;
            p.max_price_per_m2 = parseFloat(document.getElementById('cfgMaxPriceM2').value) || null;
            p.min_year_built = parseInt(document.getElementById('cfgMinYearBuilt').value) || null;
            p.max_year_built = parseInt(document.getElementById('cfgMaxYearBuilt').value) || null;
            const blRaw = document.getElementById('cfgBlacklist').value;
            p.blacklist_keywords = blRaw.split(',').map(s => s.trim().toLowerCase()).filter(s => s.length > 0);
            p.discord_webhook_url = document.getElementById('cfgDiscordWebhook').value.trim() || null;

            const finishSel = Array.from(document.querySelectorAll('.cfg-finish:checked')).map(cb => cb.value);
            p.allowed_finish_conditions = finishSel.length > 0 ? finishSel : ["all"];
            p.building_types = Array.from(document.querySelectorAll('.cfg-building:checked')).map(cb => cb.value);
            const heatingSel = Array.from(document.querySelectorAll('.cfg-heating:checked')).map(cb => cb.value);
            p.allowed_heating_types = heatingSel.length > 0 ? heatingSel : ["all"];
            p.reject_septic_tank = document.getElementById('cfgRejectSeptic').checked;
            p.allow_visualisations = document.getElementById('cfgAllowVis').checked;
            p.reject_flood_risk = !!document.getElementById('cfgRejectFlood')?.checked;
            p.reject_landslide_risk = !!document.getElementById('cfgRejectLandslide')?.checked;
            p.reject_high_voltage = !!document.getElementById('cfgRejectHV')?.checked;
            const minFrontRaw = document.getElementById('cfgMinFront')?.value;
            p.min_parcel_front_m = (minFrontRaw !== undefined && minFrontRaw !== null && String(minFrontRaw).trim() !== '') ? parseFloat(minFrontRaw) : null;

            if (cat === 'dom') {
                p.min_area_home = parseFloat(document.getElementById('cfgMinAreaHome').value) || 0;
                p.max_area_home = parseFloat(document.getElementById('cfgMaxAreaHome').value) || null;
                p.min_area_plot = parseFloat(document.getElementById('cfgMinAreaPlot').value) || 0;
                p.max_area_plot = parseFloat(document.getElementById('cfgMaxAreaPlot').value) || null;
            } else if (cat === 'mieszkanie') {
                p.min_area_home = parseFloat(document.getElementById('cfgMinAreaApt').value) || 0;
                p.max_area_home = parseFloat(document.getElementById('cfgMaxAreaApt').value) || null;
                p.min_rooms = parseInt(document.getElementById('cfgMinRooms').value) || null;
                p.max_rooms = parseInt(document.getElementById('cfgMaxRooms').value) || null;
                const minF = document.getElementById('cfgMinFloor').value;
                p.min_floor = minF !== '' ? parseInt(minF) : null;
                const maxF = document.getElementById('cfgMaxFloor').value;
                p.max_floor = maxF !== '' ? parseInt(maxF) : null;
            } else if (cat === 'dzialka') {
                p.min_area_plot = parseFloat(document.getElementById('cfgMinAreaPlotOnly').value) || 0;
                p.max_area_plot = parseFloat(document.getElementById('cfgMaxAreaPlotOnly').value) || null;
            }
        }

        function openConfigModal() {
            if (!activeConfig) return;
            switchConfigTab('profiles');

            const sc = scrapersConfig || {};
            document.getElementById('cfgScraperOtodom').checked = sc.otodom ? sc.otodom.enabled !== false : true;
            document.getElementById('cfgPagesOtodom').value = sc.otodom?.max_pages || 5;
            document.getElementById('cfgScraperOlx').checked = sc.olx ? sc.olx.enabled !== false : true;
            document.getElementById('cfgPagesOlx').value = sc.olx?.max_pages || 5;
            document.getElementById('cfgScraperNieruchomosci').checked = sc.nieruchomosci_online ? sc.nieruchomosci_online.enabled !== false : true;
            document.getElementById('cfgPagesNieruchomosci').value = sc.nieruchomosci_online?.max_pages || 5;
            document.getElementById('cfgScraperMorizon').checked = sc.morizon ? sc.morizon.enabled !== false : true;
            document.getElementById('cfgPagesMorizon').value = sc.morizon?.max_pages || 5;
            document.getElementById('cfgRequestDelay').value = sc.request_delay ?? 1.0;

            const sched = activeConfig.scheduler || {};
            if (document.getElementById('cfgSchedulerEnabled')) {
                const schedOn = sched.enabled !== false;
                document.getElementById('cfgSchedulerEnabled').checked = schedOn;
                toggleSchedulerInputs(schedOn);
            }
            if (document.getElementById('cfgIntervalMinutes')) {
                document.getElementById('cfgIntervalMinutes').value = sched.interval_minutes || 20;
            }
            if (document.getElementById('cfgNightMode')) {
                document.getElementById('cfgNightMode').checked = sched.night_mode !== false;
                toggleNightModeInputs(sched.night_mode !== false);
            }
            if (document.getElementById('cfgQuietStart')) {
                document.getElementById('cfgQuietStart').value = sched.quiet_hours_start || '22:00';
            }
            if (document.getElementById('cfgQuietEnd')) {
                document.getElementById('cfgQuietEnd').value = sched.quiet_hours_end || '07:00';
            }
            if (document.getElementById('cfgNightIntervalMinutes')) {
                document.getElementById('cfgNightIntervalMinutes').value = sched.night_interval_minutes || 60;
            }

            if (document.getElementById('cfgLlmAnalysis')) {
                document.getElementById('cfgLlmAnalysis').checked = !!activeConfig.llm_analysis_enabled;
            }
            if (document.getElementById('cfgLlmProvider')) {
                let prov = activeConfig.llm_provider || 'auto';
                if (prov === 'ollama' || prov === 'local_openai') {
                    prov = 'local';
                }
                document.getElementById('cfgLlmProvider').value = prov;
                if (typeof onLlmProviderChange === 'function') onLlmProviderChange(prov);
            }
            const localPreset = activeConfig.local_llm_preset || 'ollama';
            if (typeof setLocalEnginePreset === 'function') {
                setLocalEnginePreset(localPreset, false);
            }
            const locModel = activeConfig.local_llm_model || activeConfig.ollama_model || 'qwen2.5:7b';
            if (document.getElementById('cfgLocalModel')) {
                document.getElementById('cfgLocalModel').value = locModel;
                if (typeof syncLocalModelSelectWithInput === 'function') syncLocalModelSelectWithInput(locModel);
            }
            if (document.getElementById('cfgLocalBaseUrl')) {
                let resolvedUrl = activeConfig.local_llm_base_url;
                if (localPreset === 'ollama') {
                    if (!resolvedUrl || resolvedUrl.includes(':1234')) {
                        resolvedUrl = activeConfig.ollama_base_url || 'http://localhost:11434';
                    }
                } else if (!resolvedUrl) {
                    if (localPreset === 'lmstudio') resolvedUrl = 'http://localhost:1234/v1';
                    else if (localPreset === 'vllm') resolvedUrl = 'http://localhost:8000/v1';
                    else if (localPreset === 'docker') resolvedUrl = 'http://localhost:8080/v1';
                    else resolvedUrl = 'http://localhost:11434';
                }
                document.getElementById('cfgLocalBaseUrl').value = resolvedUrl;
            }
            if (document.getElementById('cfgLocalTimeout')) {
                document.getElementById('cfgLocalTimeout').value = activeConfig.local_llm_timeout_seconds ?? activeConfig.ollama_timeout_seconds ?? 180;
            }
            if (document.getElementById('cfgLocalTemperature')) {
                document.getElementById('cfgLocalTemperature').value = activeConfig.local_llm_temperature ?? activeConfig.ollama_temperature ?? 0.0;
            }
            if (document.getElementById('cfgLocalNumCtx')) {
                document.getElementById('cfgLocalNumCtx').value = String(activeConfig.local_llm_num_ctx ?? activeConfig.ollama_num_ctx ?? 8192);
            }
            if (document.getElementById('cfgCloudTimeout')) {
                document.getElementById('cfgCloudTimeout').value = activeConfig.cloud_llm_timeout_seconds ?? 30;
            }
            if (document.getElementById('cfgOpenRouterModel')) {
                document.getElementById('cfgOpenRouterModel').value = activeConfig.openrouter_model || 'google/gemini-2.5-flash-lite:nitro';
            }

            const capex = activeConfig.capex || {};
            if (document.getElementById('cfgCapexDeveloper')) {
                document.getElementById('cfgCapexDeveloper').value = capex.developer_rate ?? 1800;
            }
            if (document.getElementById('cfgCapexRenovation')) {
                document.getElementById('cfgCapexRenovation').value = capex.renovation_rate ?? 2200;
            }
            if (document.getElementById('cfgCapexAgency')) {
                document.getElementById('cfgCapexAgency').value = String(capex.agency_fee_pct ?? 2);
            }
            if (document.getElementById('cfgCapexPccExempt')) {
                document.getElementById('cfgCapexPccExempt').checked = !!capex.pcc_exempt_first_home;
            }

            if (!currentProfileId && allProfiles.length > 0) {
                currentProfileId = allProfiles[0].id;
            }
            refreshProfileSelect();
            loadProfileIntoForm(currentProfileId);

            document.getElementById('configModal').classList.add('open');
            document.body.classList.add('config-open');
            closeProfileMenu();
        }

        function closeConfigModal(e) {
            if (!e || e.target.id === 'configModal' || e === null) {
                document.getElementById('configModal').classList.remove('open');
                document.body.classList.remove('config-open');
            }
        }

        function setCfgCity(city) {
            document.getElementById('cfgCity').value = city;
        }

        async function saveConfiguration(triggerScrapingImmediately = false) {
            saveCurrentFormIntoMemory();

            const scrapersPayload = {
                otodom: {
                    enabled: document.getElementById('cfgScraperOtodom').checked,
                    max_pages: parseInt(document.getElementById('cfgPagesOtodom').value) || 5
                },
                olx: {
                    enabled: document.getElementById('cfgScraperOlx').checked,
                    max_pages: parseInt(document.getElementById('cfgPagesOlx').value) || 5
                },
                nieruchomosci_online: {
                    enabled: document.getElementById('cfgScraperNieruchomosci').checked,
                    max_pages: parseInt(document.getElementById('cfgPagesNieruchomosci').value) || 5
                },
                morizon: {
                    enabled: document.getElementById('cfgScraperMorizon').checked,
                    max_pages: parseInt(document.getElementById('cfgPagesMorizon').value) || 5
                },
                request_delay: parseFloat(document.getElementById('cfgRequestDelay').value) || 1.0
            };

            const schedulerPayload = {
                enabled: document.getElementById('cfgSchedulerEnabled')?.checked ?? true,
                interval_minutes: parseInt(document.getElementById('cfgIntervalMinutes')?.value) || 20,
                night_mode: document.getElementById('cfgNightMode')?.checked ?? true,
                quiet_hours_start: document.getElementById('cfgQuietStart')?.value || '22:00',
                quiet_hours_end: document.getElementById('cfgQuietEnd')?.value || '07:00',
                night_interval_minutes: parseInt(document.getElementById('cfgNightIntervalMinutes')?.value) || 60
            };

            const capexPayload = {
                developer_rate: parseFloat(document.getElementById('cfgCapexDeveloper')?.value) || 1800,
                renovation_rate: parseFloat(document.getElementById('cfgCapexRenovation')?.value) || 2200,
                agency_fee_pct: parseFloat(document.getElementById('cfgCapexAgency')?.value ?? '2') || 0,
                pcc_exempt_first_home: !!document.getElementById('cfgCapexPccExempt')?.checked
            };

            const localBaseUrl = document.getElementById('cfgLocalBaseUrl')?.value?.trim() || document.getElementById('cfgOllamaBaseUrl')?.value?.trim() || 'http://localhost:11434';
            const localModel = document.getElementById('cfgLocalModel')?.value?.trim() || document.getElementById('cfgOllamaModel')?.value?.trim() || 'qwen2.5:7b';
            const localTimeout = parseFloat(document.getElementById('cfgLocalTimeout')?.value || document.getElementById('cfgOllamaTimeout')?.value) || 180;
            const localTemp = parseFloat(document.getElementById('cfgLocalTemperature')?.value || document.getElementById('cfgOllamaTemperature')?.value) || 0.0;
            const localCtx = parseInt(document.getElementById('cfgLocalNumCtx')?.value || document.getElementById('cfgOllamaNumCtx')?.value, 10) || 8192;
            const localKey = activeConfig.local_llm_api_key || 'not-needed';
            const localPreset = document.getElementById('cfgLocalPreset')?.value || 'ollama';
            const cloudTimeout = parseFloat(document.getElementById('cfgCloudTimeout')?.value) || 30;

            const payload = {
                profiles: allProfiles,
                scrapers: scrapersPayload,
                scheduler: schedulerPayload,
                capex: capexPayload,
                llm_analysis_enabled: document.getElementById('cfgLlmAnalysis')?.checked ?? false,
                llm_provider: document.getElementById('cfgLlmProvider')?.value || 'auto',
                local_llm_preset: localPreset,
                local_llm_base_url: localBaseUrl,
                local_llm_model: localModel,
                local_llm_api_key: localKey,
                local_llm_timeout_seconds: localTimeout,
                local_llm_temperature: localTemp,
                local_llm_num_ctx: localCtx,
                cloud_llm_timeout_seconds: cloudTimeout,
                ollama_model: localModel,
                ollama_base_url: localBaseUrl,
                ollama_timeout_seconds: localTimeout,
                ollama_temperature: localTemp,
                ollama_num_ctx: localCtx,
                openrouter_model: document.getElementById('cfgOpenRouterModel')?.value?.trim() || 'google/gemini-2.5-flash-lite:nitro'
            };

            try {
                activeConfig = await Transport.saveConfig(payload);
                allProfiles = activeConfig.profiles || allProfiles;
                scrapersConfig = activeConfig.scrapers || scrapersPayload;
                closeConfigModal(null);
                showToast("Zapisano konfigurację");

                await fetchConfig();

                if (triggerScrapingImmediately) {
                    triggerScrape();
                } else {
                    await fetchListings();
                }
            } catch (err) {
                console.error("Failed to save config:", err);
                showToast("Błąd zapisu konfiguracji.");
            }
        }

        async function resetDatabaseData() {
            const scope = currentProfileId || null;
            const scopeLabel = scope ? `profilu "${scope}"` : 'CAŁEJ bazy danych';
            const typed = prompt(
                `UWAGA: Usuniesz WSZYSTKIE oferty z ${scopeLabel}\n(wraz z historią cen, notatkami i statusami CRM).\n\nAby potwierdzić, wpisz: RESET`,
                ''
            );
            if (typed !== 'RESET') {
                showToast('Anulowano — nie wpisano RESET.');
                return;
            }
            try {
                const out = await Transport.resetData({ confirm: true, profile: scope });
                showToast(`Usunięto ${out.deleted_listings} ofert (zakres: ${out.scope}).`);
                await fetchListings();
            } catch (e) {
                console.error('Reset failed:', e);
                showToast('Błąd resetu bazy danych.');
            }
        }

        // ========================
        // Listings fetching & filtering
        // ========================
        async function fetchListings() {
            lastFetchAt = Date.now();
            try {
                const out = await Transport.fetchListings(fetchEtag);
                if (out.status === 304) return;

                const newEtag = out.etag;
                const data = out.data;

                const newSigs = {};
                data.forEach(i => { newSigs[i.id] = JSON.stringify(i); });

                const firstLoad = allListings.length === 0 && fetchEtag === null;
                allListings = data;

                const changed = new Set();
                const removed = new Set();
                if (!firstLoad) {
                    for (const id of Object.keys(lastServerSigs)) {
                        if (!(id in newSigs)) removed.add(+id);
                        else if (lastServerSigs[id] !== newSigs[id]) changed.add(+id);
                    }
                    for (const id of Object.keys(newSigs)) {
                        if (!(id in lastServerSigs)) changed.add(+id);
                    }
                }
                lastServerSigs = newSigs;
                fetchEtag = newEtag;

                renderProfileTabs();
                const baseListings = getListingsForActiveProfile();
                updateStats(baseListings);

                if (firstLoad) {
                    applyFilters();
                    return;
                }
                if (changed.size === 0 && removed.size === 0) return;
                applyFiltersIncremental(changed, removed);
            } catch (err) {
                console.error("Failed to load listings:", err);
                showToast("Błąd ładowania ofert z bazy danych.");
                if (allListings.length === 0) {
                    const container = document.getElementById('listingsContainer');
                    if (container) {
                        container.innerHTML = `
                            <div style="text-align: center; padding: 48px 24px; color: var(--text-muted);">
                                <div style="font-size: 28px; margin-bottom: 12px;">⚠️</div>
                                <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 6px;">Nie udało się załadować ofert z bazy danych</div>
                                <div style="font-size: 13px; max-width: 420px; margin: 0 auto 16px;">Wystąpił problem podczas pobierania danych z serwera. Sprawdź logi kontenera lub spróbuj ponownie.</div>
                                <button class="btn btn-secondary" onclick="fetchListings()" style="display: inline-flex; align-items: center; gap: 6px; cursor: pointer;">
                                    Odśwież dane
                                </button>
                            </div>
                        `;
                    }
                }
            }
        }

        let currentPerspective = 'ALL';

        function setViewMode(perspectiveVal) {
            currentPerspective = perspectiveVal;
            const sel = document.getElementById('perspectiveSelect');
            if (sel) sel.value = perspectiveVal;
            const selMob = document.getElementById('filterPerspective');
            if (selMob) selMob.value = perspectiveVal;
            applyFilters();
        }

        function updateStats(items) {
            const safeSet = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.innerText = val;
            };

            const countAll = items.length;
            const countNew = items.filter(i => i.is_new_cycle).length;
            const countUpdated = items.filter(i => i.is_updated_cycle).length;
            const countReview = items.filter(i => i.user_status === 'NEW').length;
            const countChecked = items.filter(i => i.user_status === 'CHECKED').length;

            const pSel = document.getElementById('perspectiveSelect');
            const pSelMob = document.getElementById('filterPerspective');
            [pSel, pSelMob].forEach(s => {
                if (s) {
                    const setOpt = (val, txt) => {
                        const o = s.querySelector(`option[value="${val}"]`);
                        if (o) o.textContent = txt;
                    };
                    setOpt('ALL', `Cała baza (${countAll})`);
                    setOpt('NEW_CYCLE', `Ostatni przebieg (${countNew})`);
                    setOpt('UPDATED_CYCLE', `Korekty cen (${countUpdated})`);
                    setOpt('TO_REVIEW', `Do zbadania (${countReview})`);
                    setOpt('CHECKED', `Sprawdzone (${countChecked})`);
                }
            });

            let viewItems = items;
            if (currentPerspective === 'NEW_CYCLE') {
                viewItems = items.filter(i => i.is_new_cycle);
            } else if (currentPerspective === 'UPDATED_CYCLE') {
                viewItems = items.filter(i => i.is_updated_cycle);
            } else if (currentPerspective === 'TO_REVIEW') {
                viewItems = items.filter(i => i.user_status === 'NEW');
            } else if (currentPerspective === 'CHECKED') {
                viewItems = items.filter(i => i.user_status === 'CHECKED');
            }

            const total = viewItems.length;
            const favs = viewItems.filter(i => i.user_status === 'FAVORITE').length;
            const toVisit = viewItems.filter(i => i.user_status === 'TO_VISIT').length;
            const wl = viewItems.filter(i => i.qualification_status === 'QUALIFIED_WHITELIST' && i.user_status !== 'REJECTED').length;
            const qual = viewItems.filter(i => i.is_qualified && i.user_status !== 'REJECTED').length;
            const rev = viewItems.filter(i => i.qualification_status === 'NEEDS_REVIEW' && i.user_status !== 'REJECTED').length;
            const border = viewItems.filter(i => i.qualification_status === 'NEEDS_REVIEW_BORDERLINE' && i.user_status !== 'REJECTED').length;
            const rej = viewItems.filter(i => i.user_status === 'REJECTED' || i.qualification_status.startsWith('REJECTED')).length;
            const newCnt = viewItems.filter(i => i.is_new_cycle).length;

            safeSet('stTotal', total);
            safeSet('cntTotalAll', total);
            safeSet('stNew', newCnt);
            safeSet('stFavorite', favs);
            safeSet('stToVisit', toVisit);
            safeSet('stWhitelist', wl);
            safeSet('stQualified', qual);
            safeSet('cntRev', rev);
            safeSet('cntBorder', border);
            safeSet('cntRej', rej);

            const priced = viewItems.filter(i => i.price_per_m2 > 0);
            const avg = priced.length > 0 ? priced.reduce((acc, c) => acc + c.price_per_m2, 0) / priced.length : 0;
            safeSet('stAvgPrice', avg > 0 ? Math.round(avg).toLocaleString('pl-PL') + ' zł/m²' : '—');

            // Fade pipeline items with 0 count
            document.querySelectorAll('.pipe-item').forEach(btn => {
                const b = btn.querySelector('b');
                const isZero = b && (b.innerText.trim() === '0' || b.innerText.trim() === '');
                btn.classList.toggle('pipe-empty', isZero && !btn.classList.contains('active'));
            });

            const countEl = document.getElementById('profileDropCount');
            if (countEl) countEl.innerText = countAll;
        }

        function setFilter(filterVal, el) {
            document.querySelectorAll('.pipe-item').forEach(b => b.classList.toggle('active', b.dataset.filter === filterVal));
            currentFilter = filterVal;
            applyFilters();
        }

        function resetLiveFilters() {
            if (document.getElementById('filterCategory')) document.getElementById('filterCategory').value = 'ALL';
            if (document.getElementById('filterProfile')) document.getElementById('filterProfile').value = selectedProfileId || 'ALL';
            if (document.getElementById('filterMaxPrice')) document.getElementById('filterMaxPrice').value = '';
            if (document.getElementById('filterMinArea')) document.getElementById('filterMinArea').value = '';
            if (document.getElementById('filterMaxArea')) document.getElementById('filterMaxArea').value = '';
            if (document.getElementById('filterMinPlot')) document.getElementById('filterMinPlot').value = '';
            if (document.getElementById('filterMarket')) document.getElementById('filterMarket').value = 'ALL';
            if (document.getElementById('filterBuildingType')) document.getElementById('filterBuildingType').value = 'ALL';
            if (document.getElementById('filterFinish')) document.getElementById('filterFinish').value = 'ALL';
            if (document.getElementById('filterVis')) document.getElementById('filterVis').value = 'ALL';
            if (document.getElementById('filterSewerage')) document.getElementById('filterSewerage').value = 'ALL';
            if (document.getElementById('filterHeating')) document.getElementById('filterHeating').value = 'ALL';
            if (document.getElementById('filterMinRooms')) document.getElementById('filterMinRooms').value = '';
            if (document.getElementById('filterMinYear')) document.getElementById('filterMinYear').value = '';
            if (document.getElementById('filterExactLoc')) document.getElementById('filterExactLoc').value = 'ALL';
            if (document.getElementById('filterPerspective')) document.getElementById('filterPerspective').value = 'ALL';
            if (document.getElementById('perspectiveSelect')) document.getElementById('perspectiveSelect').value = 'ALL';
            currentPerspective = 'ALL';
            if (document.getElementById('searchInput')) document.getElementById('searchInput').value = '';
            setFilter('ALL');
        }

        function computeFilteredItems() {
            const query = (document.getElementById('searchInput')?.value || '').toLowerCase().trim();
            const sortMode = document.getElementById('sortSelect')?.value || 'score_desc';

            const categoryVal = document.getElementById('filterCategory')?.value || 'ALL';
            const maxPriceVal = parseFloat(document.getElementById('filterMaxPrice')?.value) || null;
            const minAreaVal = parseFloat(document.getElementById('filterMinArea')?.value) || null;
            const maxAreaVal = parseFloat(document.getElementById('filterMaxArea')?.value) || null;
            const minPlotVal = parseFloat(document.getElementById('filterMinPlot')?.value) || null;
            const marketVal = document.getElementById('filterMarket')?.value || 'ALL';
            const buildingTypeVal = document.getElementById('filterBuildingType')?.value || 'ALL';
            const finishVal = document.getElementById('filterFinish')?.value || 'ALL';
            const visVal = document.getElementById('filterVis')?.value || 'ALL';
            const sewerageVal = document.getElementById('filterSewerage')?.value || 'ALL';
            const heatingVal = document.getElementById('filterHeating')?.value || 'ALL';
            const minRoomsVal = parseInt(document.getElementById('filterMinRooms')?.value) || null;
            const minYearVal = parseInt(document.getElementById('filterMinYear')?.value) || null;
            const exactLocVal = document.getElementById('filterExactLoc')?.value || 'ALL';

            const baseListings = getListingsForActiveProfile();

            let filtered = baseListings.filter(item => {
                if (currentPerspective === 'NEW_CYCLE' && !item.is_new_cycle) return false;
                if (currentPerspective === 'UPDATED_CYCLE' && !item.is_updated_cycle) return false;
                if (currentPerspective === 'TO_REVIEW' && item.user_status !== 'NEW') return false;
                if (currentPerspective === 'CHECKED' && item.user_status !== 'CHECKED') return false;

                if (currentFilter === 'CRM_FAVORITE' && item.user_status !== 'FAVORITE') return false;
                if (currentFilter === 'CRM_TO_VISIT' && item.user_status !== 'TO_VISIT') return false;
                if (currentFilter === 'QUALIFIED_WHITELIST' && (item.qualification_status !== 'QUALIFIED_WHITELIST' || item.user_status === 'REJECTED')) return false;
                if (currentFilter === 'QUALIFIED' && (!item.is_qualified || item.user_status === 'REJECTED')) return false;
                if (currentFilter === 'NEEDS_REVIEW' && (item.qualification_status !== 'NEEDS_REVIEW' || item.user_status === 'REJECTED')) return false;
                if (currentFilter === 'NEEDS_REVIEW_BORDERLINE' && (item.qualification_status !== 'NEEDS_REVIEW_BORDERLINE' || item.user_status === 'REJECTED')) return false;
                if (currentFilter === 'REJECTED' && item.user_status !== 'REJECTED' && !item.qualification_status.startsWith('REJECTED')) return false;
                if (currentFilter === 'NEW' && !item.is_new_cycle) return false;

                if (categoryVal !== 'ALL' && item.category !== categoryVal) return false;

                if (maxPriceVal && item.price > maxPriceVal) return false;
                if (minAreaVal && item.area_home < minAreaVal) return false;
                if (maxAreaVal && item.area_home > maxAreaVal) return false;
                if (minPlotVal && (item.area_plot || 0) < minPlotVal) return false;
                if (marketVal !== 'ALL' && item.market !== marketVal) return false;
                if (buildingTypeVal !== 'ALL' && item.building_type !== buildingTypeVal) return false;
                if (finishVal !== 'ALL' && item.finish_condition !== finishVal) return false;
                if (visVal === 'NO_VIS' && item.has_visualisations) return false;
                if (visVal === 'ONLY_VIS' && !item.has_visualisations) return false;
                if (sewerageVal !== 'ALL' && item.sewerage !== sewerageVal) return false;
                if (heatingVal !== 'ALL' && item.heating !== heatingVal) return false;
                if (minRoomsVal && (item.rooms === null || item.rooms === undefined || item.rooms < minRoomsVal)) return false;
                if (minYearVal && (item.year_built === null || item.year_built === undefined || item.year_built < minYearVal)) return false;
                if (exactLocVal === 'EXACT' && !item.is_exact_coords) return false;

                if (query) {
                    const haystack = [
                        item.title, item.location_raw, item.street, item.district, item.city,
                        item.building_type, item.user_notes, item.sewerage, item.heating,
                        ...(item.pros || []), ...(item.cons || []),
                        ...(item.filter_reasons || [])
                    ].join(" ").toLowerCase();
                    if (!haystack.includes(query)) return false;
                }
                return true;
            });

            if (sortMode === 'score_desc') {
                filtered.sort((a, b) => b.qualification_score - a.qualification_score);
            } else if (sortMode === 'price_asc') {
                filtered.sort((a, b) => a.price - b.price);
            } else if (sortMode === 'price_desc') {
                filtered.sort((a, b) => b.price - a.price);
            } else if (sortMode === 'area_desc') {
                filtered.sort((a, b) => b.area_home - a.area_home);
            } else if (sortMode === 'price_m2_asc') {
                filtered.sort((a, b) => a.price_per_m2 - b.price_per_m2);
            } else if (sortMode === 'created_desc') {
                filtered.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
            } else if (sortMode === 'created_asc') {
                filtered.sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));
            } else if (sortMode === 'deal_desc') {
                const devOf = (i) => (i.price_deviation_adjusted_pct ?? i.price_deviation_pct ?? 9999);
                filtered.sort((a, b) => devOf(a) - devOf(b));
            } else if (sortMode === 'drop_desc') {
                filtered.sort((a, b) => (b.price_drop_amount || 0) - (a.price_drop_amount || 0));
            }

            return filtered;
        }

        function updateFilteredCount(count, filteredItems) {
            const cntEl = document.getElementById('cntFiltered');
            if (cntEl) cntEl.innerText = count;
            const tglEl = document.getElementById('tglListCount');
            if (tglEl) tglEl.innerText = count;
            if (filteredItems && Array.isArray(filteredItems)) {
                const priced = filteredItems.filter(i => i.price_per_m2 > 0);
                const avg = priced.length > 0 ? priced.reduce((acc, c) => acc + c.price_per_m2, 0) / priced.length : 0;
                const avgEl = document.getElementById('stAvgPrice');
                if (avgEl) avgEl.innerText = avg > 0 ? Math.round(avg).toLocaleString('pl-PL') + ' zł/m²' : '—';
            }
        }

        function debouncedApplyFilters() {
            clearTimeout(searchDebounceTimer);
            searchDebounceTimer = setTimeout(applyFilters, 250);
        }

        function applyFilters() {
            const baseListings = getListingsForActiveProfile();
            updateStats(baseListings);
            const filtered = computeFilteredItems();
            updateFilteredCount(filtered.length, filtered);
            renderGrid(filtered);
            renderMapMarkers(filtered);
            renderFilterTokens();
        }

        function applyFiltersIncremental(changedSet, removedSet) {
            const baseListings = getListingsForActiveProfile();
            updateStats(baseListings);
            const filtered = computeFilteredItems();
            updateFilteredCount(filtered.length, filtered);
            renderGridIncremental(filtered, changedSet);
            updateMapMarkers(filtered, changedSet, removedSet);
            renderFilterTokens();
        }

        // ========================
        // Faceted search tokens
        // ========================
        function filterOptionText(el) {
            if (!el || !el.value) return null;
            const opt = Array.from(el.options).find(o => o.value === el.value);
            return opt ? opt.textContent.trim() : el.value;
        }

        function fmtNum(n) { return Number(n).toLocaleString('pl-PL'); }

        function renderFilterTokens() {
            const box = document.getElementById('filterTokens');
            const badge = document.getElementById('activeFilterCount');
            if (!box) return;

            const t = [];
            const add = (id, label, text) => { if (text) t.push({ id, label, text }); };
            const sel = (id, label) => {
                const el = document.getElementById(id);
                if (el && el.value && el.value !== 'ALL') add(id, label, filterOptionText(el));
            };

            sel('filterCategory', 'Kategoria');
            const maxP = parseFloat(document.getElementById('filterMaxPrice')?.value);
            if (maxP) add('filterMaxPrice', 'Cena', `≤ ${fmtNum(maxP)} zł`);
            const minA = parseFloat(document.getElementById('filterMinArea')?.value);
            const maxA = parseFloat(document.getElementById('filterMaxArea')?.value);
            if (minA || maxA) {
                const part = minA ? `≥ ${fmtNum(minA)}` : '';
                const part2 = maxA ? `≤ ${fmtNum(maxA)}` : '';
                add(minA ? 'filterMinArea' : 'filterMaxArea', 'Metraż', [part, part2].filter(Boolean).join(' ') + ' m²');
            }
            const minPlot = parseFloat(document.getElementById('filterMinPlot')?.value);
            if (minPlot) add('filterMinPlot', 'Działka', `≥ ${fmtNum(minPlot)} m²`);
            sel('filterMarket', 'Rynek');
            sel('filterBuildingType', 'Zabudowa');
            sel('filterFinish', 'Stan');
            sel('filterVis', 'Zdjęcia');
            sel('filterSewerage', 'Ścieki');
            sel('filterHeating', 'Ogrzewanie');
            const minRooms = parseInt(document.getElementById('filterMinRooms')?.value);
            if (minRooms) add('filterMinRooms', 'Pokoje', `≥ ${minRooms}`);
            const minYear = parseInt(document.getElementById('filterMinYear')?.value);
            if (minYear) add('filterMinYear', 'Rok budowy', `≥ ${minYear}`);
            sel('filterExactLoc', 'Lokalizacja');

            const xSvg = `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>`;

            box.innerHTML = t.map(tk =>
                `<button type="button" class="filter-token" onclick="clearToken('${tk.id}')" title="Usuń filtr"><span>${tk.label}</span><b>${tk.text}</b>${xSvg}</button>`
            ).join('');

            if (badge) badge.innerText = t.length;
        }

        function clearToken(id) {
            const el = document.getElementById(id);
            if (!el) return;
            if (el.tagName === 'SELECT') {
                el.value = 'ALL';
            } else {
                el.value = '';
            }
            if (id === 'filterMinArea') {
                const maxEl = document.getElementById('filterMaxArea');
                if (maxEl) maxEl.value = '';
            }
            if (id === 'filterMaxArea') {
                const minEl = document.getElementById('filterMinArea');
                if (minEl) minEl.value = '';
            }
            applyFilters();
        }

        function toggleFiltersPopover(event) {
            if (event) event.stopPropagation();
            const pop = document.getElementById('filtersPopover');
            if (!pop) return;
            pop.classList.toggle('open');
            document.body.classList.toggle('filters-open', pop.classList.contains('open'));
        }

        function closeFiltersPopover() {
            const pop = document.getElementById('filtersPopover');
            if (pop) pop.classList.remove('open');
            document.body.classList.remove('filters-open');
        }

        function formatShortPrice(price) {
            if (!price || isNaN(price) || price <= 0) return 'b/d';
            return price >= 1e6
                ? (price / 1e6).toFixed(2).replace(/\.00$/, '') + 'M'
                : price >= 1e3 ? Math.round(price / 1e3) + 'k' : String(Math.round(price));
        }

        // ========================
        // Map markers
        // ========================
        function addMapMarker(item) {
            let pinClass = "pin-blue";
            const cat = item.category || 'dom';

            if (item.user_status === 'FAVORITE') {
                pinClass = "pin-gold";
            } else if (item.user_status === 'TO_VISIT') {
                pinClass = "pin-purple";
            } else if (item.user_status === 'CHECKED') {
                pinClass = "pin-green";
            } else if (item.user_status === 'REJECTED' || (item.qualification_status && item.qualification_status.startsWith('REJECTED'))) {
                pinClass = "pin-gray";
            } else if (item.qualification_status === 'QUALIFIED_WHITELIST') {
                pinClass = "pin-green";
            } else if (item.qualification_status === 'NEEDS_REVIEW') {
                pinClass = "pin-orange";
            } else if (item.qualification_status === 'NEEDS_REVIEW_BORDERLINE') {
                pinClass = "pin-orange";
            }

            if (!item.is_exact_coords) {
                pinClass += " pin-approx";
            }

            const coordKey = `${item.latitude.toFixed(4)},${item.longitude.toFixed(4)}`;
            coordCounts[coordKey] = (coordCounts[coordKey] || 0) + 1;
            const offsetMultiplier = (coordCounts[coordKey] - 1);
            const jitterLat = item.latitude + (offsetMultiplier * 0.00015);
            const jitterLon = item.longitude + (offsetMultiplier * 0.0002);

            const shortPrice = formatShortPrice(item.price);
            const iconHtml = `<div class="custom-pin price-pin ${pinClass}" id="pin-${item.id}"><span class="pin-dot"></span><span class="pin-price">${shortPrice}</span></div>`;
            const icon = L.divIcon({
                html: iconHtml,
                className: 'custom-div-icon',
                iconSize: [54, 22],
                iconAnchor: [27, 11],
                popupAnchor: [0, -12]
            });

            const marker = L.marker([jitterLat, jitterLon], { icon: icon });

            const fallbackImg = "https://images.unsplash.com/photo-1580587771525-78b9dba3b914?auto=format&fit=crop&w=400&q=80";
            const fullImg = item.main_image_url || fallbackImg;
            const imgSrc = thumbUrl(item.main_image_url) || fallbackImg;
            const plotText = item.area_plot ? `${Math.round(item.area_plot)} m²` : 'b/d';
            const precisionText = item.is_exact_coords ? 'Lokalizacja dokładna' : 'Lokalizacja przybliżona (rejon)';
            const precisionColor = item.is_exact_coords ? 'var(--slate-text)' : 'var(--amber-text)';
            const specsText = cat === 'dzialka'
                ? `Działka: ${plotText}`
                : (cat === 'mieszkanie' ? `${item.area_home.toFixed(0)} m² • ${item.rooms ? item.rooms + ' pok. • ' : ''}` : `${item.area_home.toFixed(0)} m² • Działka: ${plotText} • `);

            const popupHtml = `
                <div class="popup-card">
                    <img src="${escapeHtml(imgSrc)}" class="popup-img" onerror="this.src='${fallbackImg}'" onclick="openImgModal('${escapeHtml(fullImg)}')">
                    <div class="popup-body">
                        <div class="popup-price num">${Math.round(item.price).toLocaleString('pl-PL')} zł</div>
                        <div class="popup-title"><a href="${escapeHtml(item.url)}" target="_blank">${escapeHtml(item.title)}</a></div>
                        <div class="popup-specs">${escapeHtml(specsText)}${escapeHtml(item.street || item.district || item.city || '')}</div>
                        <div class="popup-precision" style="color: ${precisionColor};">${precisionText}</div>
                        <div class="popup-actions">
                            <button class="btn btn-sm ${item.user_status === 'FAVORITE' ? 'active-fav' : ''}" onclick="updateStatus(${item.id}, 'FAVORITE')">★</button>
                            <button class="btn btn-sm ${item.user_status === 'TO_VISIT' ? 'active-visit' : ''}" onclick="updateStatus(${item.id}, 'TO_VISIT')">Do wizyty</button>
                            <button class="btn btn-sm" onclick="updateStatus(${item.id}, 'REJECTED')">×</button>
                            ${item.geoportal_url ? `<a href="${escapeHtml(item.geoportal_url)}" target="_blank" class="btn btn-sm" title="Geoportal">Geoportal</a>` : ''}
                        </div>
                    </div>
                </div>
            `;

            marker.bindPopup(popupHtml);
            marker.on('click', () => {
                highlightCard(item.id);
            });
            marker.on('mouseover', () => {
                const card = document.getElementById('card-' + item.id);
                if (card) card.classList.add('card-hover-highlight');
            });
            marker.on('mouseout', () => {
                const card = document.getElementById('card-' + item.id);
                if (card) card.classList.remove('card-hover-highlight');
            });

            markersGroup.addLayer(marker);
            markersMap[item.id] = marker;
            return marker;
        }

        function renderMapMarkers(items) {
            if (!map || !markersGroup) return;

            markersGroup.clearLayers();
            markersMap = {};
            coordCounts = {};

            const validCoordsItems = items.filter(i => i.latitude && i.longitude);

            validCoordsItems.forEach(addMapMarker);

            if (validCoordsItems.length > 0) {
                try {
                    if (map) map.invalidateSize();
                    if (validCoordsItems.length === 1) {
                        map.setView(
                            [validCoordsItems[0].latitude, validCoordsItems[0].longitude],
                            15
                        );
                    } else {
                        map.fitBounds(markersGroup.getBounds(), { padding: [40, 40], maxZoom: 15 });
                    }
                } catch (e) {}
            }
            if (aqiLayerActive) {
                renderAqiMapLayer(items);
            }
        }

        function toggleAqiMapLayer() {
            if (!map) return;
            aqiLayerActive = !aqiLayerActive;
            const btn = document.getElementById('btnToggleAqiLayer');
            if (btn) {
                btn.classList.toggle('active', aqiLayerActive);
            }
            renderAqiMapLayer(allListings);
        }

        function renderAqiMapLayer(items = null) {
            if (!map) return;
            if (!aqiLayer) {
                aqiLayer = L.featureGroup();
            }
            aqiLayer.clearLayers();

            if (!aqiLayerActive) {
                if (map.hasLayer(aqiLayer)) {
                    map.removeLayer(aqiLayer);
                }
                return;
            }

            if (!map.hasLayer(aqiLayer)) {
                aqiLayer.addTo(map);
            }

            const targetItems = items || allListings || [];
            targetItems.forEach(item => {
                if (!item.latitude || !item.longitude) return;
                const aqi = item.air_aqi;
                let color = '#64748b';
                if (aqi !== null && aqi !== undefined) {
                    if (aqi <= 20) color = '#22c55e';
                    else if (aqi <= 40) color = '#84cc16';
                    else if (aqi <= 60) color = '#eab308';
                    else if (aqi <= 80) color = '#f97316';
                    else if (aqi <= 100) color = '#ef4444';
                    else color = '#7f1d1d';
                } else if (item.air_smog_risk === 'WYSOKIE') {
                    color = '#ef4444';
                } else if (item.air_smog_risk === 'SREDNIE') {
                    color = '#eab308';
                } else if (item.air_smog_risk === 'NISKIE') {
                    color = '#22c55e';
                }

                const circle = L.circleMarker([item.latitude, item.longitude], {
                    radius: 12,
                    fillColor: color,
                    fillOpacity: 0.82,
                    color: '#ffffff',
                    weight: 2,
                });

                const aqiText = aqi !== null && aqi !== undefined ? `AQI ${aqi}` : 'Brak danych';
                const labelText = item.air_aqi_label ? ` (${escapeHtml(item.air_aqi_label)})` : '';
                const winterText = item.air_pm25_heating_avg ? `<br/>PM2.5 zima: <strong>${item.air_pm25_heating_avg} µg/m³</strong>` : '';
                const smogText = item.air_smog_days ? `<br/>Dni smogowe: <strong>${item.air_smog_days} dni/rok</strong>` : '';

                circle.bindTooltip(`
                    <div style="font-size:11px;line-height:1.35;">
                        <strong>${escapeHtml(item.title || 'Oferta')}</strong><br/>
                        Jakość powietrza: <strong>${aqiText}</strong>${labelText}
                        ${winterText}
                        ${smogText}
                    </div>
                `, { direction: 'top', offset: [0, -8] });

                circle.on('click', () => {
                    openAiModal(item.id);
                });

                aqiLayer.addLayer(circle);
            });
        }

        function updateMapMarkers(items, changedSet, removedSet) {
            if (!map || !markersGroup) return;

            const wanted = new Set(items.map(i => i.id));

            for (const id of Object.keys(markersMap)) {
                if (!wanted.has(+id) || changedSet.has(+id)) {
                    markersGroup.removeLayer(markersMap[id]);
                    delete markersMap[id];
                }
            }

            for (const item of items) {
                if (!item.latitude || !item.longitude) continue;
                if (!markersMap[item.id]) addMapMarker(item);
            }
        }

        function highlightCard(id) {
            let card = document.getElementById('card-' + id);
            if (!card && currentGridItems && currentGridItems.length > 0) {
                const targetIdx = currentGridItems.findIndex(i => i.id === id);
                if (targetIdx >= 0) {
                    while (renderedGridCount <= targetIdx && renderedGridCount < currentGridItems.length) {
                        appendNextGridChunk(gridRenderToken);
                    }
                    card = document.getElementById('card-' + id);
                }
            }
            if (card) {
                card.scrollIntoView({ behavior: 'smooth', block: 'center' });
                card.classList.add('card-highlight');
                setTimeout(() => { card.classList.remove('card-highlight'); }, 1500);
            }
        }

        function highlightMapMarker(id, enable) {
            const marker = markersMap[id];
            if (!marker) return;

            if (enable) {
                marker.setZIndexOffset(10000);
                const el = marker.getElement();
                if (el) {
                    const pin = el.querySelector('.custom-pin');
                    if (pin) pin.classList.add('pin-highlighted');
                }
            } else {
                marker.setZIndexOffset(0);
                const el = marker.getElement();
                if (el) {
                    const pin = el.querySelector('.custom-pin');
                    if (pin) pin.classList.remove('pin-highlighted');
                }
            }
        }

        function locateOnMap(id, lat, lon) {
            if (!lat || !lon) {
                showToast("Brak współrzędnych GPS dla tej oferty.");
                return;
            }
            if (currentViewMode === 'grid') {
                switchViewMode('split');
            }
            if (map) {
                map.flyTo([lat, lon], 15, { duration: 0.8 });
                const marker = markersMap[id];
                if (marker) {
                    setTimeout(() => { marker.openPopup(); }, 700);
                }
            }
        }

        // ========================
        // Gallery / lightbox
        // ========================
        let activeModalGallery = [];
        let activeModalIndex = 0;

        function previewCardThumb(cardId, src, thumbEl, idx, total) {
            const img = document.getElementById('card-img-' + cardId);
            if (img) img.src = proxyImg(src);
            const counter = document.getElementById('imgcount-' + cardId);
            if (counter && total) counter.innerText = `${idx + 1}/${total}`;
            if (thumbEl && thumbEl.parentElement) {
                thumbEl.parentElement.querySelectorAll('.card-thumb').forEach(t => t.classList.remove('active'));
                thumbEl.classList.add('active');
            }
        }

        function openListingGallery(itemId, startIndex) {
            const item = allListings.find(i => i.id === itemId);
            if (!item) return;
            const fallbackImg = "https://images.unsplash.com/photo-1580587771525-78b9dba3b914?auto=format&fit=crop&w=600&q=80";
            const gallery = (item.gallery_images && item.gallery_images.length > 0) ? item.gallery_images : [item.main_image_url || fallbackImg];
            activeModalGallery = gallery;
            activeModalIndex = Math.max(0, Math.min(startIndex || 0, gallery.length - 1));
            showModalImage();
            const m = document.getElementById('imgModal');
            if (m) m.classList.add('open');
        }

        function openImgModal(src) {
            activeModalGallery = [src];
            activeModalIndex = 0;
            showModalImage();
            const m = document.getElementById('imgModal');
            if (m) m.classList.add('open');
        }

        function showModalImage() {
            const img = document.getElementById('imgModalSrc');
            const cap = document.getElementById('imgModalCaption');
            if (!img || activeModalGallery.length === 0) return;
            img.src = proxyImg(activeModalGallery[activeModalIndex]);
            if (cap) {
                cap.innerText = `${activeModalIndex + 1} / ${activeModalGallery.length}`;
            }
            const prevBtn = document.getElementById('imgModalPrev');
            const nextBtn = document.getElementById('imgModalNext');
            if (prevBtn) prevBtn.style.display = activeModalGallery.length > 1 ? 'flex' : 'none';
            if (nextBtn) nextBtn.style.display = activeModalGallery.length > 1 ? 'flex' : 'none';
        }

        function navModalGallery(direction) {
            if (activeModalGallery.length <= 1) return;
            activeModalIndex = (activeModalIndex + direction + activeModalGallery.length) % activeModalGallery.length;
            showModalImage();
        }

        function closeImgModal() {
            const m = document.getElementById('imgModal');
            if (m) m.classList.remove('open');
        }

        // ========================
        // Grid rendering (Progressive batch loading / Virtualized DOM)
        // ========================
        let currentGridItems = [];
        let renderedGridCount = 0;
        let gridObserver = null;
        let gridScrollBound = false;
        const GRID_CHUNK = 24;

        function renderGrid(items) {
            const container = document.getElementById('listingsContainer');
            if (!container) return;

            const token = ++gridRenderToken;
            container.innerHTML = '';
            currentGridItems = items || [];
            renderedGridCount = 0;

            if (gridObserver) {
                gridObserver.disconnect();
                gridObserver = null;
            }

            if (currentGridItems.length === 0) {
                container.innerHTML = '<div style="text-align: center; padding: 60px 20px; color: var(--text-muted); font-size: var(--font-size-base);">Brak ofert spełniających aktywne kryteria wyszukiwania.</div>';
                return;
            }

            appendNextGridChunk(token);
            bindGridScroll();
        }

        function appendNextGridChunk(token) {
            if (token !== undefined && token !== gridRenderToken) return;
            const container = document.getElementById('listingsContainer');
            if (!container) return;

            if (renderedGridCount >= currentGridItems.length) {
                const existingSentinel = document.getElementById('gridSentinel');
                if (existingSentinel) existingSentinel.remove();
                if (gridObserver) {
                    gridObserver.disconnect();
                    gridObserver = null;
                }
                return;
            }

            const slice = currentGridItems.slice(renderedGridCount, renderedGridCount + GRID_CHUNK);
            const chunkStartIndex = renderedGridCount;
            renderedGridCount += slice.length;

            let sentinel = document.getElementById('gridSentinel');
            if (!sentinel) {
                sentinel = document.createElement('div');
                sentinel.id = 'gridSentinel';
                sentinel.style.cssText = 'height: 40px; width: 100%; grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; color: var(--text-muted); font-size: 12px;';
                container.appendChild(sentinel);
            }

            sentinel.insertAdjacentHTML('beforebegin', slice.map((item, i) => buildCardHtml(item, i === 0 && chunkStartIndex === 0)).join(''));

            if (renderedGridCount < currentGridItems.length) {
                sentinel.innerHTML = `<span style="opacity: 0.6;">Ładowanie kolejnych ofert (${renderedGridCount}/${currentGridItems.length})...</span>`;
                if (!gridObserver && window.IntersectionObserver) {
                    gridObserver = new IntersectionObserver((entries) => {
                        if (entries[0] && entries[0].isIntersecting) {
                            appendNextGridChunk(gridRenderToken);
                        }
                    }, { rootMargin: '300px 0px' });
                    gridObserver.observe(sentinel);
                }
            } else {
                sentinel.remove();
                if (gridObserver) {
                    gridObserver.disconnect();
                    gridObserver = null;
                }
            }
        }

        function bindGridScroll() {
            if (gridScrollBound) return;
            gridScrollBound = true;
            const container = document.getElementById('listingsContainer');

            const checkScroll = () => {
                if (renderedGridCount >= currentGridItems.length) return;
                const sentinel = document.getElementById('gridSentinel');
                if (!sentinel) return;

                if (container && container.scrollHeight > container.clientHeight) {
                    if (container.scrollTop + container.clientHeight >= container.scrollHeight - 350) {
                        appendNextGridChunk(gridRenderToken);
                    }
                } else {
                    const rect = sentinel.getBoundingClientRect();
                    if (rect.top <= window.innerHeight + 350) {
                        appendNextGridChunk(gridRenderToken);
                    }
                }
            };

            if (container) {
                container.addEventListener('scroll', checkScroll, { passive: true });
            }
            window.addEventListener('scroll', checkScroll, { passive: true });
        }

        function renderGridIncremental(items, changedSet) {
            const container = document.getElementById('listingsContainer');
            if (!container) return;

            if (items.length === 0) {
                renderGrid(items);
                return;
            }

            if (!container.querySelector('article.card') || changedSet.size > 20 || Math.abs(items.length - currentGridItems.length) > 5) {
                renderGrid(items);
                return;
            }

            gridRenderToken++;
            currentGridItems = items;
            const wantedIds = items.map(i => i.id);
            const wantedSet = new Set(wantedIds);

            container.querySelectorAll('article.card').forEach(n => {
                const cardId = parseInt(n.id.replace('card-', ''), 10);
                if (!wantedSet.has(cardId)) n.remove();
            });

            // Update visible cards that changed
            items.slice(0, renderedGridCount).forEach(item => {
                if (changedSet.has(item.id)) {
                    const existing = document.getElementById('card-' + item.id);
                    if (existing) {
                        const tmp = document.createElement('div');
                        tmp.innerHTML = buildCardHtml(item);
                        if (tmp.firstElementChild) existing.replaceWith(tmp.firstElementChild);
                    }
                }
            });
        }

        // ========================
        // Card builder
        // ========================
        function svgIcon(name, size = 13) {
            const s = `width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"`;
            const icons = {
                'star': `<svg ${s}><path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"></path></svg>`,
                'calendar': `<svg ${s}><path d="M8 2v4"></path><path d="M16 2v4"></path><rect width="18" height="18" x="3" y="4" rx="2"></rect><path d="M3 10h18"></path></svg>`,
                'check': `<svg ${s}><path d="M20 6 9 17l-5-5"></path></svg>`,
                'x': `<svg ${s}><path d="M18 6 6 18"></path><path d="m6 6 12 12"></path></svg>`,
                'note': `<svg ${s}><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"></path><path d="M14 2v4a2 2 0 0 0 2 2h4"></path><path d="M16 13H8"></path><path d="M16 17H8"></path><path d="M10 9H8"></path></svg>`,
                'map-pin': `<svg ${s}><path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"></path><circle cx="12" cy="10" r="3"></circle></svg>`,
                'shield': `<svg ${s}><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"></path><path d="m9 12 2 2 4-4"></path></svg>`,
                'external': `<svg ${s}><path d="M15 3h6v6"></path><path d="M10 14 21 3"></path><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path></svg>`,
                'chevron-down': `<svg ${s}><path d="m6 9 6 6 6-6"></path></svg>`,
                'more': `<svg ${s}><circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle><circle cx="5" cy="12" r="1"></circle></svg>`,
                'plug': `<svg ${s}><path d="M12 22v-5"></path><path d="M9 8V2"></path><path d="M15 8V2"></path><path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"></path></svg>`,
                'copy': `<svg ${s}><rect width="14" height="14" x="8" y="8" rx="2" ry="2"></rect><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"></path></svg>`,
                'alert-triangle': `<svg ${s}><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>`
            };
            return icons[name] || '';
        }

        function buildCardHtml(item, isFirstCard = false) {
            const fallbackImg = "https://images.unsplash.com/photo-1580587771525-78b9dba3b914?auto=format&fit=crop&w=600&q=80";

            let badgeClass = "badge-rejected";
            let badgeLabel = "Odrzucona";

            if (item.qualification_status === 'QUALIFIED_WHITELIST') {
                badgeClass = "badge-whitelist";
                badgeLabel = "Whitelist";
            } else if (item.is_qualified) {
                badgeClass = "badge-qualified";
                badgeLabel = "Zakwalifikowana";
            } else if (item.qualification_status === 'NEEDS_REVIEW') {
                badgeClass = "badge-review";
                badgeLabel = "Do weryfikacji";
            } else if (item.qualification_status === 'NEEDS_REVIEW_BORDERLINE') {
                badgeClass = "badge-review";
                badgeLabel = "Graniczna";
            }

            let cardCrmClass = "";
            if (item.user_status === 'FAVORITE') {
                cardCrmClass = "is-favorite";
            } else if (item.user_status === 'TO_VISIT') {
                cardCrmClass = "is-tovisit";
            } else if (item.user_status === 'CHECKED') {
                cardCrmClass = "is-checked";
            } else if (item.user_status === 'REJECTED') {
                cardCrmClass = "is-rejected";
            }

            // Physical status on photo: ONLY physical lifecycle (Nowa, Re-list, Wycofana, Korekta)
            let deltaBadge = '';
            let deltaPill = '';
            if (item.relist_count && item.relist_count > 0) {
                deltaBadge = `<span class="card-badge badge-relist" title="Wykryto powrót oferty na rynek (${item.relist_count}x re-listing)">🔁 Re-list (${item.relist_count}x)</span>`;
                deltaPill = `<span class="meta-tag tag-profile" style="color: #ef4444; border-color: rgba(239, 68, 68, 0.4);">Re-list ${item.relist_count}x</span>`;
            } else if (item.listing_status === 'DELISTED') {
                deltaBadge = `<span class="card-badge badge-delisted">Wycofana</span>`;
                deltaPill = `<span class="meta-tag tag-profile">Wycofana</span>`;
            } else if (item.is_new_cycle) {
                deltaBadge = `<span class="card-badge badge-new">Nowa</span>`;
                deltaPill = `<span class="meta-tag tag-exact">Nowa</span>`;
            } else if (item.price_drop_amount && item.price_drop_amount > 0) {
                deltaBadge = `<span class="card-badge badge-updated">−${item.price_drop_amount.toLocaleString('pl-PL')} zł</span>`;
                deltaPill = `<span class="meta-tag tag-profile">Korekta −${item.price_drop_pct}%</span>`;
            } else if (item.is_updated_cycle) {
                deltaBadge = `<span class="card-badge badge-updated">Korekta</span>`;
                deltaPill = `<span class="meta-tag tag-profile">Zaktualizowana</span>`;
            }

            const plotText = item.area_plot ? `${Math.round(item.area_plot)} m²` : '';
            const imgSrc = thumbUrl(item.main_image_url) || fallbackImg;
            const imgLoading = isFirstCard ? 'eager' : 'lazy';
            const imgFetchPriority = isFirstCard ? ' fetchpriority="high"' : '';

            const prosHtml = (item.pros || []).slice(0, 2).map(p => `<li>${escapeHtml(p)}</li>`).join('');
            const consHtml = (item.cons || []).slice(0, 1).map(c => `<li class="warning">${escapeHtml(c)}</li>`).join('');

            const precisionTag = item.is_exact_coords
                ? `<span class="loc-precision tag-exact">Dokładna</span>`
                : `<span class="loc-precision tag-approx">Rejon</span>`;

            const notesBadge = item.user_notes ? ` (1)` : '';

            const cat = item.category || 'dom';
            const catLabel = cat === 'mieszkanie' ? 'Mieszkanie' : (cat === 'dzialka' ? 'Działka' : 'Dom');
            const profileBadge = item.profile_name ? `<span class="meta-tag tag-profile">${escapeHtml(item.profile_name)}</span>` : '';
            const ownerBadge = item.is_private_owner === true ? `<span class="meta-tag tag-exact">Prywatne</span>` : (item.is_private_owner === false ? `<span class="meta-tag tag-approx">Biuro / deweloper</span>` : '');

            let aqiTag = '';
            if (item.air_aqi !== null && item.air_aqi !== undefined) {
                const aqiVal = item.air_aqi;
                let aqiCls = 'tag-aqi-good';
                if (aqiVal > 60 || item.air_smog_risk === 'WYSOKIE') {
                    aqiCls = 'tag-aqi-danger';
                } else if (aqiVal > 40 || item.air_smog_risk === 'SREDNIE') {
                    aqiCls = 'tag-aqi-warn';
                }
                const label = item.air_aqi_label || `AQI ${aqiVal}`;
                const winterNote = item.air_pm25_heating_avg ? ` (PM2.5 zima: ${item.air_pm25_heating_avg} µg/m³)` : '';
                aqiTag = `<span class="meta-tag tag-aqi ${aqiCls}" title="Jakość powietrza CAMS: AQI ${aqiVal} - ${escapeHtml(label)}${winterNote}">AQI ${aqiVal}</span>`;
            }

            let rejectionHtml = "";
            if (item.filter_reasons && item.filter_reasons.length > 0 && !item.is_qualified) {
                rejectionHtml = `
                    <div class="rejection-box">
                        <span class="rejection-label">Kryterium wykluczające</span>
                        <ul>${item.filter_reasons.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
                    </div>
                `;
            }

            const marketText = item.market && item.market !== 'nieokreślony' ? escapeHtml(item.market) : '';
            const finishText = item.finish_condition && item.finish_condition !== 'nieokreślony' ? escapeHtml(item.finish_condition) : '';
            const sewText = item.sewerage && item.sewerage !== 'nieznana' ? escapeHtml(item.sewerage) : '';
            const heatText = item.heating && item.heating !== 'nieznane' ? escapeHtml(item.heating) : '';
            const roadText = item.access_road_type && item.access_road_type !== 'nieznana' ? escapeHtml(item.access_road_type) : '';
            const visTag = item.has_visualisations ? `<span class="meta-tag tag-vis">Wizualizacje</span>` : '';
            const mpzpZoneText = item.mpzp_zone ? escapeHtml(item.mpzp_zone.length > 25 ? item.mpzp_zone.slice(0, 25) + '…' : item.mpzp_zone) : '';
            const floodWarn = item.flood_risk_zone === 'ZAGROZENIE_POWODZIOWE';

            // Contextual 3×3 technical grid — always exactly 9 cells, '—' fallback for missing data
            const specCell = (label, value) => {
                const has = value !== null && value !== undefined && String(value).trim() !== '';
                return `<div class="spec-cell"><span class="spec-label">${label}</span><span class="spec-value${has ? '' : ' spec-value-empty'}">${has ? value : '—'}</span></div>`;
            };

            const yearText = item.year_built ? `${item.year_built}` : '';
            const roomsText = (item.rooms !== null && item.rooms !== undefined) ? `${item.rooms}` : '';
            const floorText = (item.floor !== null && item.floor !== undefined)
                ? `${item.floor}${(item.floors_in_building !== null && item.floors_in_building !== undefined) ? '/' + item.floors_in_building : ''}`
                : '';
            const daysText = (item.days_on_market !== null && item.days_on_market !== undefined) ? `${item.days_on_market} dni` : '';
            const mediaText = item.has_fiber
                ? 'Światłowód'
                : (item.broadband_status && item.broadband_status !== 'BRAK_ZASIĘGU' ? escapeHtml(item.broadband_status) : '');
            const frontText = item.parcel_front_width_m
                ? `${item.parcel_front_width_m} m${item.parcel_length_m ? ` × ~${item.parcel_length_m} m` : ''}`
                : '';
            const shapeText = item.parcel_shape_type
                ? `${escapeHtml(item.parcel_shape_type)}${item.parcel_aspect_ratio ? ` (1:${item.parcel_aspect_ratio})` : ''}`
                : '';
            const roadMpzpText = [roadText, mpzpZoneText].filter(Boolean).join(' · ');
            const gesutNets = item.gesut_networks?.networks ?? {};
            const gesutText = Object.keys(gesutNets).filter(k => gesutNets[k]).join(' · ');

            let specsHtml = '';
            if (cat === 'mieszkanie') {
                specsHtml = [
                    specCell('Metraż', item.area_home > 0 ? `${item.area_home.toFixed(1)} m²` : ''),
                    specCell('Pokoje', roomsText),
                    specCell('Piętro', floorText),
                    specCell('Rok budowy', yearText),
                    specCell('Rynek', marketText),
                    specCell('Stan', finishText),
                    specCell('Ogrzewanie', heatText),
                    specCell('Media', mediaText),
                    specCell('Na rynku', daysText),
                ].join('');
            } else if (cat === 'dzialka') {
                const plotFull = item.area_plot
                    ? `${Math.round(item.area_plot)} m²`
                    : (item.area_home > 0 ? `${Math.round(item.area_home)} m²` : '');
                specsHtml = [
                    specCell('Powierzchnia', plotFull),
                    specCell('Front / Wymiary', frontText),
                    specCell('Kształt', shapeText),
                    specCell('Dojazd', roadText),
                    specCell('MPZP', mpzpZoneText),
                    specCell('Media GESUT', gesutText),
                    specCell('Rynek', marketText),
                    specCell('Na rynku', daysText),
                    specCell('Ścieki', sewText),
                ].join('');
            } else {
                specsHtml = [
                    specCell('Dom', item.area_home > 0 ? `${item.area_home.toFixed(1)} m²` : ''),
                    specCell('Działka', plotText),
                    specCell('Rok budowy', yearText),
                    specCell('Zabudowa', item.building_type ? escapeHtml(item.building_type) : ''),
                    specCell('Rynek', marketText),
                    specCell('Stan', finishText),
                    specCell('Ogrzewanie', heatText),
                    specCell('Ścieki', sewText),
                    specCell('Dojazd / MPZP', roadMpzpText),
                ].join('');
            }

            const floodHtml = floodWarn
                ? `<div class="card-flood-warning">${svgIcon('alert-triangle')} Teren zalewowy — zagrożenie powodziowe (ISOK)</div>`
                : '';

            const gallery = item.gallery_images || [];
            const galleryCount = gallery.length > 0 ? gallery.length : 1;
            const galleryCountBadge = galleryCount > 1
                ? `<span class="card-media-count num" id="imgcount-${item.id}">1/${galleryCount}</span>`
                : '';

            let galleryThumbnailsHtml = '';
            if (gallery.length > 1) {
                const thumbs = gallery.slice(0, 5).map((imgUrl, idx) => `
                    <img src="${escapeHtml(thumbUrl(imgUrl))}" class="card-thumb ${idx === 0 ? 'active' : ''}"
                         alt="Miniatura ${idx + 1}" loading="lazy"
                         onmouseenter="previewCardThumb(${item.id}, '${escapeHtml(imgUrl)}', this, ${idx}, ${gallery.length})"
                         onclick="event.stopPropagation(); openListingGallery(${item.id}, ${idx})"
                         onerror="this.style.display='none'">
                `).join('');
                const moreCount = gallery.length - 5;
                const moreHtml = moreCount > 0 ? `<span class="thumb-more" onclick="event.stopPropagation(); openListingGallery(${item.id}, 5)">+${moreCount}</span>` : '';
                galleryThumbnailsHtml = `<div class="card-gallery-strip">${thumbs}${moreHtml}</div>`;
            }

            // Market delta (technical label, normalized for finish condition)
            let devBadge = '';
            if (item.price_deviation_pct !== null && item.price_deviation_pct !== undefined && item.market_median_m2) {
                const devRaw = item.price_deviation_pct;
                const dev = (item.price_deviation_adjusted_pct !== null && item.price_deviation_adjusted_pct !== undefined)
                    ? item.price_deviation_adjusted_pct
                    : devRaw;
                const medFmt = Math.round(item.market_median_m2).toLocaleString('pl-PL');
                const corrNote = (dev !== devRaw)
                    ? ` — po korekcie o stan wykończenia: ${dev > 0 ? '+' : ''}${dev}% (surowe: ${devRaw > 0 ? '+' : ''}${devRaw}%)`
                    : '';
                if (dev <= -4.0) {
                    devBadge = `<span class="market-delta dev-low" title="Mediana rynku: ${medFmt} zł/m²${corrNote} — wycena poniżej mediany">${dev > 0 ? '+' : ''}${dev}% vs rynek</span>`;
                } else if (dev >= 8.0) {
                    devBadge = `<span class="market-delta dev-high" title="Mediana rynku: ${medFmt} zł/m²${corrNote} — wycena powyżej mediany">+${dev}% vs rynek</span>`;
                } else {
                    devBadge = `<span class="market-delta dev-fair" title="Mediana rynku: ${medFmt} zł/m²${corrNote} — wycena w normie">${dev > 0 ? '+' : ''}${dev}% vs rynek</span>`;
                }
            }

            const priceDropHtml = item.price_drop_amount
                ? `<span class="price-drop num" title="Skumulowana obniżka ceny">−${item.price_drop_amount.toLocaleString('pl-PL')} zł (${item.price_drop_pct}%)</span>`
                : '';

            // Days on market badge (e.g. "14 dni") — always visible on the card
            const daysBadge = (item.days_on_market !== null && item.days_on_market !== undefined)
                ? `<span class="market-delta dev-fair" title="Liczba dni od pierwszego wykrycia oferty">⏱ ${item.days_on_market} dni</span>`
                : '';

            // Price per ar (for plots and houses with land) next to price per m²
            let priceArHtml = '';
            if (item.area_plot && item.area_plot >= 100 && item.price > 0) {
                const pricePerAr = Math.round(item.price / (item.area_plot / 100));
                priceArHtml = `<span class="price-m2 num" title="Cena za ar działki (${Math.round(item.area_plot)} m²)">${pricePerAr.toLocaleString('pl-PL')} zł/ar</span>`;
            }

            const tcoSub = (item.capex_total !== null && item.capex_total !== undefined)
                ? `<span class="card-action-sub num">CAPEX ~${formatPrice(item.capex_total)}</span>`
                : '';

            const geoportalHref = item.geoportal_url
                ? item.geoportal_url
                : (item.latitude && item.longitude
                    ? `https://mapy.geoportal.gov.pl/imap/Imgp_2.html?locale=pl&gui=new&session=%7B%22actions%22%3A%5B%7B%22name%22%3A%22locatePoint%22%2C%22params%22%3A%7B%22x%22%3A${item.longitude}%2C%22y%22%3A${item.latitude}%2C%22srid%22%3A4326%7D%7D%5D%7D`
                    : null);

            const gesutUrl = item.gesut_url || geoportalHref;

            const crmMenu = `
                <div class="card-action-menu">
                    <button class="card-action" onclick="toggleCardMenu(event, ${item.id})" title="Akcje CRM">
                        ${svgIcon('more')} Akcje ${svgIcon('chevron-down', 11)}
                    </button>
                    <div class="menu-popover" id="cardmenu-${item.id}">
                        <button class="menu-item ${item.user_status === 'FAVORITE' ? 'active' : ''}" onclick="toggleStatus(${item.id}, 'FAVORITE')">${svgIcon('star')} Ulubione</button>
                        <button class="menu-item ${item.user_status === 'TO_VISIT' ? 'active' : ''}" onclick="toggleStatus(${item.id}, 'TO_VISIT')">${svgIcon('calendar')} Do wizyty</button>
                        <button class="menu-item ${item.user_status === 'CHECKED' ? 'active' : ''}" onclick="toggleStatus(${item.id}, 'CHECKED')">${svgIcon('check')} Oznacz jako sprawdzone</button>
                        <button class="menu-item" onclick="toggleNotes(${item.id})">${svgIcon('note')} Notatka${notesBadge}</button>
                        ${gesutUrl ? `<a class="menu-item" href="${escapeHtml(gesutUrl)}" target="_blank" rel="noopener noreferrer" title="Uzbrojenie terenu GESUT (woda, prąd, gaz, kanalizacja)">${svgIcon('plug')} Uzbrojenie GESUT</a>` : ''}
                        <button class="menu-item" onclick="locateOnMap(${item.id}, ${item.latitude || 'null'}, ${item.longitude || 'null'})">${svgIcon('map-pin')} Pokaż na mapie</button>
                        <button class="menu-item danger ${item.user_status === 'REJECTED' ? 'active' : ''}" onclick="toggleStatus(${item.id}, 'REJECTED')">${svgIcon('x')} ${item.user_status === 'REJECTED' ? 'Przywróć' : 'Odrzuć'}</button>
                    </div>
                </div>
            `;

            return `
            <article class="card ${cardCrmClass}" id="card-${item.id}" onmouseenter="highlightMapMarker(${item.id}, true)" onmouseleave="highlightMapMarker(${item.id}, false)">
                <div class="card-media" onclick="openListingGallery(${item.id}, 0)">
                    <img id="card-img-${item.id}" src="${escapeHtml(imgSrc)}" alt="Zdjęcie nieruchomości" loading="${imgLoading}" decoding="async"${imgFetchPriority} onerror="this.src='${fallbackImg}'">
                    ${galleryCountBadge}
                    <div class="card-media-topbar">
                        <div class="card-badges-group">
                            ${deltaBadge}
                        </div>
                        <button type="button" class="card-fav-btn ${item.user_status === 'FAVORITE' ? 'active' : ''}" onclick="event.stopPropagation(); toggleStatus(${item.id}, 'FAVORITE')" title="Ulubione">
                            ${item.user_status === 'FAVORITE' ? '★' : '☆'}
                        </button>
                    </div>
                </div>
                ${galleryThumbnailsHtml}
                <div class="card-content">
                    <div class="card-meta-row">
                        <span class="meta-source"><b>${catLabel}</b> · ${escapeHtml(item.portal).toUpperCase()} · #${escapeHtml(item.portal_id)}</span>
                        <span class="workflow-badge ${badgeClass}">${badgeLabel}</span>
                        ${profileBadge}
                        ${ownerBadge}
                        ${visTag}
                        ${deltaPill}
                        ${aqiTag}
                        <span class="meta-score">Score <strong>${Math.round(item.qualification_score)}</strong>/150</span>
                    </div>

                    <h3 class="card-title">
                        <a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title)}</a>
                    </h3>

                    <div class="card-price-row">
                        <span class="price-main">${Math.round(item.price).toLocaleString('pl-PL')} zł</span>
                        <span class="price-m2">(${Math.round(item.price_per_m2).toLocaleString('pl-PL')} zł/m²)</span>
                        ${priceArHtml}
                        ${devBadge}
                        ${daysBadge}
                        ${priceDropHtml}
                    </div>

                    <div class="card-location">
                        <span>${escapeHtml(item.street || item.district || item.city || item.location_raw || '')}</span>
                        ${precisionTag}
                    </div>

                    <div class="card-specs-grid-fixed">
                        ${specsHtml}
                    </div>

                    ${floodHtml}

                    ${rejectionHtml}

                    ${(prosHtml || consHtml) ? `<div class="card-features"><ul>${prosHtml}${consHtml}</ul></div>` : ''}

                    <div class="card-footer">
                        <button class="card-action primary" onclick="openAiModal(${item.id})" title="Raport audytu i due diligence">
                            ${svgIcon('shield')} Audyt ${tcoSub}
                        </button>
                        ${geoportalHref ? `
                        <a href="${escapeHtml(geoportalHref)}" class="card-action" target="_blank" rel="noopener noreferrer" title="${item.parcel_id ? 'Działka katastralna: ' + escapeHtml(item.parcel_id) : 'Otwórz punkt w Geoportalu'}">
                            ${svgIcon('map-pin')} Geoportal
                        </a>` : ''}
                        <a href="${escapeHtml(item.url)}" class="card-action" target="_blank" rel="noopener noreferrer" title="Otwórz ogłoszenie źródłowe">
                            ${svgIcon('external')} Otwórz źródło
                        </a>
                        ${crmMenu}
                    </div>

                    <div class="notes-drawer" id="notes-${item.id}">
                        <textarea id="note-txt-${item.id}" placeholder="Prywatne notatki z oględzin, agent, ustalenia…">${escapeHtml(item.user_notes || '')}</textarea>
                        <div class="notes-footer">
                            <span class="notes-status-text" id="note-st-${item.id}"></span>
                            <button class="notes-save-btn" onclick="saveNote(${item.id})">Zapisz</button>
                        </div>
                    </div>
                </div>
            </article>
            `;
        }

        function toggleCardMenu(event, id) {
            event.stopPropagation();
            const m = document.getElementById('cardmenu-' + id);
            if (!m) return;
            const wasOpen = m.classList.contains('open');
            closeCardMenus();
            if (!wasOpen) m.classList.add('open');
        }

        function closeCardMenus() {
            document.querySelectorAll('.menu-popover.open').forEach(m => m.classList.remove('open'));
        }

        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
        }

        function formatPrice(val) {
            if (val === null || val === undefined || isNaN(val)) return '0 zł';
            return Math.round(Number(val)).toLocaleString('pl-PL') + ' zł';
        }

        // Route an image through the first-party /img proxy (removes third-party cookies).
        function proxyImg(url) {
            if (!url) return url;
            return '/img?url=' + encodeURIComponent(url);
        }

        // Rewrite portal CDN URLs to a small thumbnail variant for cards.
        // OLX / Otodom (apollo.olxcdn.com) support on-the-fly resize via ";s=WxH".
        function thumbUrl(url, size) {
            if (!url) return url;
            const s = size || '480x360';
            let out = url;
            if (url.indexOf('apollo.olxcdn.com') !== -1 && /;s=\d+x\d+/i.test(url)) {
                out = url.replace(/;s=\d+x\d+/i, ';s=' + s).replace(/;q=\d+/, ';q=70');
            }
            return proxyImg(out);
        }

        // ========================
        // CRM actions
        // ========================
        async function toggleStatus(id, targetStatus) {
            const item = allListings.find(i => i.id === id);
            if (!item) return;
            const newStatus = (item.user_status === targetStatus) ? 'NEW' : targetStatus;
            await updateStatus(id, newStatus);
            closeCardMenus();
        }

        async function updateStatus(id, newStatus) {
            try {
                await Transport.updateStatus(id, newStatus);
                const item = allListings.find(i => i.id === id);
                if (item) item.user_status = newStatus;
                updateStats(allListings);
                applyFiltersIncremental(new Set([id]), new Set());
                showToast(`Status oferty #${id}: ${newStatus}`);
            } catch (err) {
                console.error("Status update failed:", err);
                showToast("Błąd zapisu statusu.");
            }
        }

        function toggleNotes(id) {
            const drawer = document.getElementById('notes-' + id);
            if (drawer) {
                drawer.style.display = (drawer.style.display === 'block') ? 'none' : 'block';
            }
            closeCardMenus();
        }

        async function saveNote(id) {
            const txt = document.getElementById('note-txt-' + id).value;
            const stLabel = document.getElementById('note-st-' + id);
            stLabel.innerText = "Zapisywanie…";

            try {
                await Transport.updateNotes(id, txt);
                const item = allListings.find(i => i.id === id);
                if (item) item.user_notes = txt;
                stLabel.innerText = "Zapisano";
                showToast("Notatka zapisana.");
                setTimeout(() => { stLabel.innerText = ""; }, 2000);
            } catch (err) {
                stLabel.innerText = "Błąd zapisu";
                showToast("Nie udało się zapisać notatki.");
            }
        }

        // ========================
        // Scraping state & monitoring
        // ========================
        let scrapePollTimer = null;
        let currentLogFilter = 'all';
        let lastScrapeStatus = null;

        function isLogSuccess(l) {
            return l.category === 'success' || l.level === 'success' || (l.message && (l.message.includes('[Zakwalifikowano]') || l.message.includes('⭐')));
        }

        function isLogRejected(l) {
            return l.category === 'rejected' || l.level === 'rejected' || (l.message && l.message.includes('[Odrzucono]'));
        }

        function isLogGeo(l) {
            return l.category === 'geo' || l.level === 'geo' || (l.message && (l.message.includes('[Geokoder]') || l.message.includes('[Geoportal]') || l.message.includes('[Rejestry]') || l.message.includes('[Backfill]') || l.message.includes('[SIDUSIS]')));
        }

        function isLogAi(l) {
            return l.category === 'ai' || l.level === 'ai' || (l.message && (l.message.includes('[AI Audit]') || l.message.includes('[AI]')));
        }

        function isLogError(l) {
            if (isLogRejected(l) || isLogSuccess(l) || isLogGeo(l) || isLogAi(l)) {
                return false;
            }
            return l.category === 'error' || l.level === 'error' || l.category === 'warning' || l.level === 'warning' || (l.message && (l.message.includes('Błąd') || l.message.includes('🛑')));
        }

        function setLogFilter(filterName, btn) {
            currentLogFilter = filterName;
            const container = document.getElementById('progLogFilters');
            if (container) {
                container.querySelectorAll('.log-filter-btn').forEach(b => b.classList.remove('active'));
            }
            if (btn) btn.classList.add('active');
            if (lastScrapeStatus) {
                renderScrapeStatus(lastScrapeStatus);
            }
        }
        window.setLogFilter = setLogFilter;

        function formatLogEntry(l) {
            const time = escapeHtml(l.time || '');
            const rawMsg = l.message || '';
            const level = l.level || 'info';
            const cat = l.category || 'info';

            let portalBadge = '';
            let msgText = rawMsg;
            const portalMatch = rawMsg.match(/^\[([^\]]+)\]\s*(.*)$/);
            if (portalMatch) {
                portalBadge = `<span class="log-portal">${escapeHtml(portalMatch[1])}</span>`;
                msgText = portalMatch[2];
            }

            let levelBadge = '';
            if (isLogGeo(l)) {
                levelBadge = `<span class="log-level-badge log-level-geo">REJESTRY</span>`;
            } else if (isLogRejected(l)) {
                levelBadge = `<span class="log-level-badge log-level-rejected">ODRZUCONA</span>`;
            } else if (isLogAi(l)) {
                levelBadge = `<span class="log-level-badge log-level-ai">AI AUDIT</span>`;
            } else if (isLogSuccess(l)) {
                levelBadge = `<span class="log-level-badge log-level-success">KWALIFIKACJA</span>`;
            } else if (level === 'error' || cat === 'error' || (rawMsg && rawMsg.includes('Błąd'))) {
                levelBadge = `<span class="log-level-badge log-level-error">BŁĄD</span>`;
            } else if (level === 'warning' || cat === 'warning' || (rawMsg && rawMsg.includes('🛑'))) {
                levelBadge = `<span class="log-level-badge log-level-warning">UWAGA</span>`;
            }

            let escapedMsg = escapeHtml(msgText);
            escapedMsg = escapedMsg.replace(/(\d[\d\s,.]*\s*(?:zł|PLN|m²|m2))/g, '<strong>$1</strong>');

            return `
                <div class="log-row">
                    <span class="log-time">${time}</span>
                    ${levelBadge}
                    ${portalBadge}
                    <span class="log-msg">${escapedMsg}</span>
                </div>
            `;
        }

        function renderScrapeStatus(st) {
            const panel = document.getElementById('progressPanel');
            const btnScrape = document.getElementById('btnScrape');
            const btnCancel = document.getElementById('btnCancelScrape');

            if (!panel || !st) return;
            lastScrapeStatus = st;

            const pct = Math.min(100, Math.max(0, st.percentage || 0));
            document.getElementById('progPercent').innerText = pct + '%';
            document.getElementById('progBar').style.width = pct + '%';
            if (st.is_running) {
                document.getElementById('progStep').innerText = st.current_step || 'Przetwarzanie…';
                document.getElementById('progPortal').innerText = st.current_portal ? `Aktywny: ${st.current_portal}` : 'Inicjalizacja…';
            } else {
                document.getElementById('progStep').innerText = st.current_step || 'Zakończono';
                document.getElementById('progPortal').innerText = '';
            }

            document.getElementById('progScraped').innerText = st.items_scraped || 0;
            document.getElementById('progQualified').innerText = st.items_qualified || 0;
            document.getElementById('progDups').innerText = st.duplicates_found || 0;
            const elapsed = st.elapsed_seconds || 0;
            document.getElementById('progElapsed').innerText =
                elapsed >= 60 ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s` : `${elapsed}s`;

            const allLogs = st.logs || [];
            // Update log filter counts
            const allCnt = allLogs.length;
            const succCnt = allLogs.filter(isLogSuccess).length;
            const geoCnt = allLogs.filter(isLogGeo).length;
            const aiCnt = allLogs.filter(isLogAi).length;
            const rejCnt = allLogs.filter(isLogRejected).length;
            const errCnt = allLogs.filter(isLogError).length;

            const elAll = document.getElementById('cntLogAll');
            if (elAll) elAll.innerText = allCnt;
            const elSucc = document.getElementById('cntLogSuccess');
            if (elSucc) elSucc.innerText = succCnt;
            const elGeo = document.getElementById('cntLogGeo');
            if (elGeo) elGeo.innerText = geoCnt;
            const elAi = document.getElementById('cntLogAi');
            if (elAi) elAi.innerText = aiCnt;
            const elRej = document.getElementById('cntLogRejected');
            if (elRej) elRej.innerText = rejCnt;
            const elErr = document.getElementById('cntLogError');
            if (elErr) elErr.innerText = errCnt;

            let filteredLogs = allLogs;
            if (currentLogFilter === 'success') {
                filteredLogs = allLogs.filter(isLogSuccess);
            } else if (currentLogFilter === 'geo') {
                filteredLogs = allLogs.filter(isLogGeo);
            } else if (currentLogFilter === 'ai') {
                filteredLogs = allLogs.filter(isLogAi);
            } else if (currentLogFilter === 'rejected') {
                filteredLogs = allLogs.filter(isLogRejected);
            } else if (currentLogFilter === 'error') {
                filteredLogs = allLogs.filter(isLogError);
            }

            const logsHtml = filteredLogs.map(formatLogEntry).join('');
            const logBox = document.getElementById('progLogs');
            if (logBox) {
                logBox.innerHTML = logsHtml || '<div class="log-row" style="color:var(--text-muted);font-style:italic;padding:4px 0;">Brak zdarzeń w tej kategorii.</div>';
                logBox.scrollTop = logBox.scrollHeight;
            }

            if (btnCancel) {
                if (st.is_running) {
                    btnCancel.style.display = 'inline-flex';
                    if (st.cancel_requested) {
                        btnCancel.disabled = true;
                        btnCancel.innerText = 'Zatrzymywanie…';
                    } else {
                        btnCancel.disabled = false;
                        btnCancel.innerText = 'Zatrzymaj';
                    }
                } else {
                    btnCancel.style.display = 'none';
                }
            }

            if (btnScrape) {
                if (st.is_running) {
                    setScrapeButtonState(st.cancel_requested ? 'stopping' : 'running');
                } else {
                    setScrapeButtonState('idle');
                }
            }
        }

        const SCRAPE_IDLE_HTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M3 5v14a9 3 0 0 0 18 0V5"></path><path d="M3 12a9 3 0 0 0 18 0"></path></svg><span class="btn-label">Synchronizuj bazę</span>';
        const SCRAPE_RUNNING_HTML = '<span class="spinner-inline" aria-hidden="true"></span><span class="btn-label">Synchronizacja w toku…</span>';
        const SCRAPE_STOPPING_HTML = '<span class="spinner-inline" aria-hidden="true"></span><span class="btn-label">Zatrzymywanie…</span>';

        function setScrapeButtonState(state) {
            const btn = document.getElementById('btnScrape');
            if (!btn) return;
            btn.classList.toggle('is-running', state !== 'idle');
            if (state === 'idle') {
                btn.disabled = false;
                btn.innerHTML = SCRAPE_IDLE_HTML;
                btn.setAttribute('aria-label', 'Synchronizuj bazę');
            } else if (state === 'starting') {
                btn.disabled = true;
                btn.innerHTML = SCRAPE_RUNNING_HTML;
                btn.setAttribute('aria-label', 'Inicjalizacja synchronizacji');
            } else if (state === 'running') {
                btn.disabled = true;
                btn.innerHTML = SCRAPE_RUNNING_HTML;
                btn.setAttribute('aria-label', 'Synchronizacja w toku');
            } else if (state === 'stopping') {
                btn.disabled = true;
                btn.innerHTML = SCRAPE_STOPPING_HTML;
                btn.setAttribute('aria-label', 'Zatrzymywanie synchronizacji');
            }
        }

        function isMobileViewport() {
            return window.matchMedia('(max-width: 767px)').matches;
        }

        function syncProgressSheetState() {
            const panel = document.getElementById('progressPanel');
            if (!panel) return;
            const expandedOnMobile = isMobileViewport() && !panel.classList.contains('collapsed') && panel.style.display !== 'none';
            document.body.classList.toggle('progress-sheet-open', expandedOnMobile);
        }

        function applyMobileProgressDefault() {
            const panel = document.getElementById('progressPanel');
            if (!panel) return;
            if (isMobileViewport() && !panel.dataset.userToggled) {
                panel.classList.add('collapsed');
                const btn = document.getElementById('btnCollapseProgress');
                if (btn) btn.setAttribute('aria-expanded', 'false');
            }
            syncProgressSheetState();
        }

        function toggleProgressPanel() {
            const panel = document.getElementById('progressPanel');
            if (!panel) return;
            const collapsed = panel.classList.toggle('collapsed');
            panel.dataset.userToggled = '1';
            const btn = document.getElementById('btnCollapseProgress');
            if (btn) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            syncProgressSheetState();
            if (map) map.invalidateSize();
        }

        window.addEventListener('resize', () => {
            syncProgressSheetState();
        });
        window.addEventListener('orientationchange', () => {
            setTimeout(syncProgressSheetState, 250);
        });

        function startScrapeMonitor() {
            if (scrapePollTimer) return;
            const panel = document.getElementById('progressPanel');
            if (panel) {
                panel.style.display = 'block';
                applyMobileProgressDefault();
            }
            if (map) map.invalidateSize();

            scrapePollTimer = setInterval(async () => {
                try {
                    const st = await Transport.scrapeStatus();
                    if (!st || typeof st !== 'object') return;
                    renderScrapeStatus(st);

                    if (!st.is_running) {
                        clearInterval(scrapePollTimer);
                        scrapePollTimer = null;

                        setScrapeButtonState('idle');
                        const btnCancel = document.getElementById('btnCancelScrape');
                        if (btnCancel) btnCancel.style.display = 'none';

                        if (st.cancel_requested || (st.current_step && st.current_step.includes('Zatrzymano'))) {
                            showToast("Synchronizacja została przerwana przez użytkownika.");
                        } else {
                            showToast(`Synchronizacja zakończona. Pobrane: ${st.items_scraped || 0}, zakwalifikowane: ${st.items_qualified || 0}`);
                        }
                        await fetchListings();
                    }
                } catch (e) {
                    console.error("Error polling scrape status:", e);
                }
            }, 600);
        }

        async function checkActiveScrape() {
            try {
                const st = await Transport.scrapeStatus();
                if (st && st.is_running) {
                    const panel = document.getElementById('progressPanel');
                    if (panel) {
                        panel.style.display = 'block';
                        applyMobileProgressDefault();
                    }
                    if (map) map.invalidateSize();
                    renderScrapeStatus(st);
                    startScrapeMonitor();
                }
            } catch (e) {
                console.debug("Could not check active scrape status:", e);
            }
        }

        async function triggerScrape() {
            const panel = document.getElementById('progressPanel');
            setScrapeButtonState('starting');
            if (panel) {
                panel.style.display = 'block';
                applyMobileProgressDefault();
            }
            if (map) map.invalidateSize();

            const curProf = allProfiles.find(p => p.id === selectedProfileId);
            const profName = curProf ? curProf.name : 'Wszystkie profile';
            showToast(`Rozpoczynanie synchronizacji (${profName})…`);

            try {
                const out = await Transport.startScrape(selectedProfileId);
                if (out.conflict) {
                    showToast("Synchronizacja jest już w toku.");
                }
                setScrapeButtonState('running');
                startScrapeMonitor();
            } catch (err) {
                showToast("Błąd podczas uruchamiania synchronizacji: " + err);
                setScrapeButtonState('idle');
            }
        }

        async function cancelScrape() {
            const btnCancel = document.getElementById('btnCancelScrape');
            if (btnCancel) {
                btnCancel.disabled = true;
                btnCancel.innerText = "Zatrzymywanie…";
            }
            showToast("Żądanie zatrzymania synchronizacji…");

            try {
                const out = await Transport.cancelScrape();
                if (out.status === 'not_running') {
                    showToast("Synchronizacja nie jest obecnie uruchomiona.");
                    setScrapeButtonState('idle');
                    if (btnCancel) btnCancel.style.display = 'none';
                    const progStep = document.getElementById('progStep');
                    if (progStep) progStep.innerText = 'Gotowy';
                    const progPortal = document.getElementById('progPortal');
                    if (progPortal) progPortal.innerText = '';
                } else {
                    showToast("Wysłano polecenie zatrzymania.");
                }
            } catch (e) {
                showToast("Nie udało się wysłać żądania zatrzymania.");
                if (btnCancel) {
                    btnCancel.disabled = false;
                    btnCancel.innerText = "Zatrzymaj";
                }
            }
        }

        // ========================
        // LLM Configuration & Diagnostics
        // ========================
        let isTestingLlm = false;
        let lastLlmStatusData = null;

        function onLlmProviderChange(val) {
            const desc = document.getElementById('llmProviderDesc');
            if (!desc) return;
            if (val === 'local' || val === 'ollama' || val === 'local_openai') {
                desc.textContent = 'Wymusza użycie lokalnego silnika AI (Ollama, LM Studio, vLLM, Docker Model Runner) na Twoim komputerze.';
            } else if (val === 'openrouter') {
                desc.textContent = 'Wymusza użycie chmurowego OpenRouter (wymaga klucza OPENROUTER_API_KEY w .env).';
            } else if (val === 'openai') {
                desc.textContent = 'Wymusza użycie oficjalnego OpenAI API (wymaga klucza OPENAI_API_KEY w .env).';
            } else {
                desc.textContent = 'Tryb automatyczny najpierw sprawdza chmurę (OpenRouter/OpenAI), a w razie braku — lokalny silnik.';
            }
        }

        function setLocalEnginePreset(preset, updateUrl = true) {
            const urlInput = document.getElementById('cfgLocalBaseUrl') || document.getElementById('cfgLocalLlmBaseUrl') || document.getElementById('cfgOllamaBaseUrl');
            const hiddenPreset = document.getElementById('cfgLocalPreset');
            const badge = document.getElementById('localPresetBadge');
            if (hiddenPreset) hiddenPreset.value = preset;

            const btns = {
                ollama: document.getElementById('btnPresetOllama'),
                lmstudio: document.getElementById('btnPresetLmStudio'),
                vllm: document.getElementById('btnPresetVllm'),
                docker: document.getElementById('btnPresetDocker')
            };
            Object.keys(btns).forEach(k => {
                if (btns[k]) {
                    if (k === preset) {
                        btns[k].classList.add('btn-primary');
                        btns[k].style.fontWeight = '600';
                    } else {
                        btns[k].classList.remove('btn-primary');
                        btns[k].style.fontWeight = 'normal';
                    }
                }
            });

            const presetLabels = {
                ollama: 'Ollama',
                lmstudio: 'LM Studio',
                vllm: 'vLLM',
                docker: 'Docker / LocalAI'
            };
            if (badge && presetLabels[preset]) {
                badge.textContent = 'Preset: ' + presetLabels[preset];
            }

            if (updateUrl && urlInput) {
                const currentVal = urlInput.value || '';
                const usesDockerHost = currentVal.includes('host.docker.internal');
                const hostPrefix = usesDockerHost ? 'http://host.docker.internal' : 'http://localhost';
                if (preset === 'ollama') {
                    urlInput.value = hostPrefix + ':11434';
                } else if (preset === 'lmstudio') {
                    urlInput.value = hostPrefix + ':1234/v1';
                } else if (preset === 'vllm') {
                    urlInput.value = hostPrefix + ':8000/v1';
                } else if (preset === 'docker') {
                    urlInput.value = hostPrefix + ':8080/v1';
                }
                showToast('Ustawiono adres silnika: ' + urlInput.value);
            }
        }
        function onLocalModelSelectChange(val) {
            const inp = document.getElementById('cfgLocalModel') || document.getElementById('cfgOllamaModel');
            if (!inp) return;
            if (val !== 'custom') {
                inp.value = val;
            } else {
                inp.focus();
                inp.select();
            }
        }
        function onLocalModelInputCustom(val) {
            const sel = document.getElementById('cfgLocalModelSelect') || document.getElementById('cfgOllamaModelSelect');
            if (!sel) return;
            const v = (val || '').trim();
            let found = false;
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value === v) {
                    sel.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found) {
                sel.value = 'custom';
            }
        }
        function syncLocalModelSelectWithInput(modelName) {
            const sel = document.getElementById('cfgLocalModelSelect') || document.getElementById('cfgOllamaModelSelect');
            if (!sel) return;
            const v = (modelName || '').trim();
            let found = false;
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value === v) {
                    sel.selectedIndex = i;
                    found = true;
                    break;
                }
            }
            if (!found && v) {
                sel.value = 'custom';
            }
        }
        function setLocalModelChip(modelName) {
            const input = document.getElementById('cfgLocalModel') || document.getElementById('cfgOllamaModel');
            if (input) {
                input.value = modelName;
            }
            syncLocalModelSelectWithInput(modelName);
        }
        function setOpenRouterModelChip(modelName) {
            const input = document.getElementById('cfgOpenRouterModel');
            if (input) {
                input.value = modelName;
            }
        }

        function updateLocalModelSelectOptions(installedModels, currentVal) {
            const sel = document.getElementById('cfgLocalModelSelect') || document.getElementById('cfgOllamaModelSelect');
            if (!sel) return;
            const defaults = ['qwen2.5:7b', 'bielik:11b-v2.3-instruct', 'qwen2.5:14b', 'llama3.1:8b', 'llama3.2:3b'];
            const allModels = Array.from(new Set([...(installedModels || []), ...defaults]));
            const cur = currentVal || document.getElementById('cfgLocalModel')?.value?.trim() || document.getElementById('cfgOllamaModel')?.value?.trim() || 'qwen2.5:7b';
            let html = allModels.map(m => {
                const isInst = (installedModels || []).includes(m);
                const tag = isInst ? ' (wykryty)' : '';
                return `<option value="${escapeHtml(m)}">${escapeHtml(m)}${tag}</option>`;
            }).join('');
            html += '<option value="custom">Inny / wpisany ręcznie...</option>';
            sel.innerHTML = html;
            syncLocalModelSelectWithInput(cur);
        }
        function checkLlmStatusIfEmpty() {
            if (!lastLlmStatusData && !isTestingLlm) {
                testLlmConnection(true);
            }
        }

        async function testLlmConnection(isAuto = false) {
            if (isTestingLlm) return;
            isTestingLlm = true;

            const btn = document.getElementById('btnTestLlm');
            const label = document.getElementById('btnTestLlmLabel');
            const container = document.getElementById('llmStatusContainer');
            const localModelInput = document.getElementById('cfgLocalModel') || document.getElementById('cfgOllamaModel');
            const requestedModel = localModelInput ? localModelInput.value.trim() : null;
            const requestedLocalUrl = document.getElementById('cfgLocalBaseUrl')?.value?.trim() || document.getElementById('cfgOllamaBaseUrl')?.value?.trim() || null;
            const requestedLocalTimeout = parseFloat(document.getElementById('cfgLocalTimeout')?.value || document.getElementById('cfgOllamaTimeout')?.value) || null;
            const requestedLocalTemp = parseFloat(document.getElementById('cfgLocalTemperature')?.value || document.getElementById('cfgOllamaTemperature')?.value) ?? null;
            const requestedLocalCtx = parseInt(document.getElementById('cfgLocalNumCtx')?.value || document.getElementById('cfgOllamaNumCtx')?.value, 10) || null;
            const requestedLocalKey = activeConfig?.local_llm_api_key || null;
            const requestedLocalPreset = document.getElementById('cfgLocalPreset')?.value || 'ollama';
            const requestedCloudTimeout = parseFloat(document.getElementById('cfgCloudTimeout')?.value) || null;
            const requestedOpenRouter = document.getElementById('cfgOpenRouterModel')?.value?.trim() || null;
            const requestedProvider = document.getElementById('cfgLlmProvider')?.value || null;

            if (btn) btn.disabled = true;
            if (label) label.innerHTML = '<span class="spinner-inline"></span> Testowanie...';
            if (container && !isAuto) {
                container.innerHTML = '<div class="llm-diag-placeholder"><span class="spinner-inline"></span> Sprawdzanie połączeń z chmurą (OpenRouter, OpenAI) oraz silnikiem lokalnym...</div>';
            }

            try {
                const data = await Transport.testLlm({
                    local_llm_preset: requestedLocalPreset,
                    local_llm_base_url: requestedLocalUrl,
                    local_llm_model: requestedModel,
                    local_llm_api_key: requestedLocalKey,
                    local_llm_timeout_seconds: requestedLocalTimeout,
                    local_llm_temperature: requestedLocalTemp,
                    local_llm_num_ctx: requestedLocalCtx,
                    cloud_llm_timeout_seconds: requestedCloudTimeout,
                    ollama_model: requestedModel,
                    ollama_base_url: requestedLocalUrl,
                    ollama_timeout_seconds: requestedLocalTimeout,
                    ollama_temperature: requestedLocalTemp,
                    ollama_num_ctx: requestedLocalCtx,
                    openrouter_model: requestedOpenRouter,
                    llm_provider: requestedProvider
                });
                lastLlmStatusData = data;
                renderLlmStatus(data);
                if (!isAuto) {
                    showToast('Zakończono test połączeń AI');
                }
            } catch (err) {
                console.error('Error testing LLM connection:', err);
                if (container) {
                    container.innerHTML = `<div class="llm-active-banner status-err"><span>Błąd zapytania testowego: ${escapeHtml(err.message)}</span></div>`;
                }
                if (!isAuto) {
                    showToast('Błąd sprawdzania statusu AI');
                }
            } finally {
                isTestingLlm = false;
                if (btn) btn.disabled = false;
                if (label) label.textContent = 'Sprawdź połączenie';
            }
        }

        function renderLlmStatus(data) {
            const container = document.getElementById('llmStatusContainer');
            if (!container) return;

            const p = data.providers || {};
            const or = p.openrouter || {};
            const oa = p.openai || {};
            const ol = p.ollama || {};
            const loc = p.local_openai || {};

            // Dynamically refresh the select with detected installed models
            const detectedModels = [
                ...(ol.installed_models || []),
                ...(loc.installed_models || [])
            ];
            if (detectedModels.length > 0) {
                updateLocalModelSelectOptions(detectedModels, document.getElementById('cfgLocalModel')?.value?.trim() || document.getElementById('cfgOllamaModel')?.value?.trim());
            }

            // Update OpenRouter key notice
            const orNotice = document.getElementById('openrouterKeyNotice');
            if (orNotice) {
                if (or.configured) {
                    const cred = (or.limit_remaining !== null && or.limit_remaining !== undefined) ? ` · Limit: $${Number(or.limit_remaining).toFixed(2)}` : '';
                    orNotice.innerHTML = `<span style="color:var(--green,#10b981);">✓ Klucz OPENROUTER_API_KEY jest aktywny (<code>${escapeHtml(or.key_masked)}</code>)${cred}</span>`;
                } else {
                    orNotice.innerHTML = '<span style="color:var(--text-muted);">ℹ️ Brak klucza OPENROUTER_API_KEY w pliku .env (opcjonalny do chmurowych modeli)</span>';
                }
            }

            let bannerHtml = '';
            if (data.has_working_provider && data.active_provider) {
                bannerHtml = `
                    <div class="llm-active-banner status-ok">
                        <span>🟢 <strong>Aktywny dostawca:</strong> ${escapeHtml(data.active_provider.label || data.active_provider.name)}</span>
                        <span style="font-size:11px;opacity:0.9;">Gotowy do analiz</span>
                    </div>
                `;
            } else {
                bannerHtml = `
                    <div class="llm-active-banner status-err">
                        <span>🔴 <strong>Brak gotowego dostawcy AI:</strong> Skonfigurowany dostawca nie odpowiada</span>
                        <span style="font-size:11px;opacity:0.9;">Sprawdź klucz API lub uruchom silnik lokalny</span>
                    </div>
                `;
            }

            function getStatusBadge(status) {
                if (status === 'ok') return '<span class="llm-provider-badge ok">Działa</span>';
                if (status === 'model_missing') return '<span class="llm-provider-badge warn">Brak modelu</span>';
                if (status === 'error' || status === 'unreachable') return '<span class="llm-provider-badge err">Błąd</span>';
                return '<span class="llm-provider-badge neutral">Nieaktywny</span>';
            }

            function getDotClass(status) {
                if (status === 'ok') return 'ok';
                if (status === 'model_missing') return 'warn';
                if (status === 'error' || status === 'unreachable') return 'err';
                return 'neutral';
            }

            const orRow = `
                <div class="llm-provider-row">
                    <div class="llm-provider-main">
                        <div class="llm-provider-title-row">
                            <span class="llm-dot ${getDotClass(or.status)}"></span>
                            <span class="llm-provider-name">OpenRouter</span>
                            <span style="color:var(--text-muted);font-size:11px;">(${escapeHtml(or.model || 'domyślny')})</span>
                            ${getStatusBadge(or.status)}
                        </div>
                        <div class="llm-provider-msg">
                            Klucz: <code>${escapeHtml(or.key_masked || 'Brak')}</code> · ${escapeHtml(or.message || '')}
                        </div>
                    </div>
                </div>
            `;

            const oaRow = `
                <div class="llm-provider-row">
                    <div class="llm-provider-main">
                        <div class="llm-provider-title-row">
                            <span class="llm-dot ${getDotClass(oa.status)}"></span>
                            <span class="llm-provider-name">OpenAI</span>
                            <span style="color:var(--text-muted);font-size:11px;">(${escapeHtml(oa.model || 'gpt-4o-mini')})</span>
                            ${getStatusBadge(oa.status)}
                        </div>
                        <div class="llm-provider-msg">
                            Klucz: <code>${escapeHtml(oa.key_masked || 'Brak')}</code> · ${escapeHtml(oa.message || '')}
                        </div>
                    </div>
                </div>
            `;



            let installedChips = '';
            if (ol.installed_models && ol.installed_models.length > 0) {
                installedChips = `
                    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">
                        Pobrane modele Ollama (kliknij, aby wybrać do konfiguracji):
                        <div class="llm-models-tags">
                            ${ol.installed_models.map(m => `<span class="llm-model-tag" onclick="setLocalModelChip('${escapeHtml(m)}')">${escapeHtml(m)}</span>`).join('')}
                        </div>
                    </div>
                `;
            }

            const tps = ol.tokens_per_second;
            let tpsBadge = '';
            if (tps) {
                const estSec = Math.round(300 / tps);
                if (tps >= 15) {
                    tpsBadge = `<span class="meta-tag tag-exact" style="margin-left:auto;" title="Akceleracja GPU (~${estSec}s na analizę oferty)">⚡ ${tps} tok/s (GPU)</span>`;
                } else {
                    tpsBadge = `<span class="meta-tag tag-vis" style="margin-left:auto;" title="Praca na CPU (~${estSec}s na analizę oferty) — rozważ mniejszy model 3B">⚠️ ${tps} tok/s (CPU)</span>`;
                }
            }

            let localChips = '';
            if (loc.installed_models && loc.installed_models.length > 0) {
                localChips = `
                    <div style="font-size:11px;color:var(--text-muted);margin-top:4px;">
                        Wykryte modele na serwerze (kliknij, aby wybrać do konfiguracji):
                        <div class="llm-models-tags">
                            ${loc.installed_models.map(m => `<span class="llm-model-tag" onclick="setLocalModelChip('${escapeHtml(m)}')">${escapeHtml(m)}</span>`).join('')}
                        </div>
                    </div>
                `;
            }

            const localAiRow = `
                <div class="llm-provider-row" style="flex-direction:column;align-items:stretch;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div class="llm-provider-title-row" style="flex:1;">
                            <span class="llm-dot ${getDotClass(loc.status)}"></span>
                            <span class="llm-provider-name">Lokalny OpenAI (LM Studio / vLLM)</span>
                            <span style="color:var(--text-muted);font-size:11px;">(${escapeHtml(loc.model || 'auto')})</span>
                            ${getStatusBadge(loc.status)}
                        </div>
                        <span style="font-size:11px;color:var(--text-muted);margin-left:8px;">${escapeHtml(loc.url || 'http://localhost:1234/v1')}</span>
                    </div>
                    <div class="llm-provider-msg" style="margin-top:4px;">
                        ${escapeHtml(loc.message || '')}
                    </div>
                    ${localChips}
                </div>
            `;

            const olRow = `
                <div class="llm-provider-row" style="flex-direction:column;align-items:stretch;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div class="llm-provider-title-row" style="flex:1;">
                            <span class="llm-dot ${getDotClass(ol.status)}"></span>
                            <span class="llm-provider-name">Ollama (lokalny)</span>
                            <span style="color:var(--text-muted);font-size:11px;">(${escapeHtml(ol.model || 'qwen2.5:7b')})</span>
                            ${getStatusBadge(ol.status)}
                            ${tpsBadge}
                        </div>
                        <span style="font-size:11px;color:var(--text-muted);margin-left:8px;">${escapeHtml(ol.url || 'http://localhost:11434')}</span>
                    </div>
                    <div class="llm-provider-msg" style="margin-top:4px;">
                        ${escapeHtml(ol.message || '')}
                    </div>
                    ${installedChips}
                </div>
            `;

            const reqProvider = document.getElementById('cfgLlmProvider')?.value || 'auto';
            const reqPreset = document.getElementById('cfgLocalPreset')?.value || 'ollama';

            const visibleRows = [];
            if (reqProvider === 'ollama' || (reqProvider === 'local' && reqPreset === 'ollama')) {
                visibleRows.push(olRow);
            } else if (reqProvider === 'local_openai' || (reqProvider === 'local' && reqPreset !== 'ollama')) {
                visibleRows.push(localAiRow);
            } else if (reqProvider === 'openrouter') {
                visibleRows.push(orRow);
            } else if (reqProvider === 'openai') {
                visibleRows.push(oaRow);
            } else {
                if (or.configured) visibleRows.push(orRow);
                if (oa.configured) visibleRows.push(oaRow);
                if (reqPreset === 'ollama') {
                    visibleRows.push(olRow);
                } else {
                    visibleRows.push(localAiRow);
                }
            }

            container.innerHTML = `
                ${bannerHtml}
                <div class="llm-provider-list">
                    ${visibleRows.join('')}
                </div>
            `;
        }

        // ========================
        // Global listeners
        // ========================
        window.addEventListener('keydown', (e) => {
            const m = document.getElementById('imgModal');
            if (m && m.classList.contains('open')) {
                if (e.key === 'ArrowLeft') {
                    e.preventDefault();
                    navModalGallery(-1);
                    return;
                } else if (e.key === 'ArrowRight') {
                    e.preventDefault();
                    navModalGallery(1);
                    return;
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    closeImgModal();
                    return;
                }
            }
            const aiM = document.getElementById('aiModal');
            if (aiM && aiM.classList.contains('open') && e.key === 'Escape') {
                e.preventDefault();
                closeAiModal(null);
                return;
            }
            const cfgM = document.getElementById('configModal');
            if (cfgM && cfgM.classList.contains('open') && e.key === 'Escape') {
                e.preventDefault();
                closeConfigModal(null);
                return;
            }
            if (e.key === 'Escape') {
                closeFiltersPopover();
                closeProfileMenu();
                closeCardMenus();
            }
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                const searchInput = document.getElementById('searchInput');
                if (searchInput) {
                    searchInput.focus();
                    searchInput.select();
                }
            }
        });

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.profile-drop')) {
                closeProfileMenu();
            }
            if (!e.target.closest('.faceted-search') && !e.target.closest('#filtersPopover')) {
                closeFiltersPopover();
            }
            if (!e.target.closest('.card-action-menu')) {
                closeCardMenus();
            }
        });

        window.addEventListener('DOMContentLoaded', async () => {
            await fetchConfig();
            await fetchListings();
            checkActiveScrape();
            ensureMapInitialized();
        });

        setInterval(() => {
            if (!document.hidden) fetchListings();
        }, 45000);

        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && Date.now() - lastFetchAt > 10000) fetchListings();
        });
