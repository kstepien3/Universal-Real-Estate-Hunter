        // ============================================================================
        // Transport module — one interface per backend operation.
        // Owns: URLs, HTTP methods, headers, JSON serialisation/parsing, the
        // ETag / 304 cycle, the 409 "already running" shape, and server error
        // message extraction. Failures are reported by throwing Error with the
        // server's message (when available); callers decide how to surface them.
        // ============================================================================
        (function () {
            'use strict';

            async function requestJson(url, options) {
                const res = await fetch(url, options);
                let data = null;
                if (res.status !== 304) {
                    try {
                        const txt = await res.text();
                        data = txt ? JSON.parse(txt) : null;
                    } catch (pe) {
                        console.warn("Could not parse response from " + url + ":", pe);
                    }
                }
                return { res: res, data: data };
            }

            function errorOf(res, data) {
                if (data && (data.error || data.message)) {
                    return new Error(String(data.error || data.message));
                }
                return new Error("HTTP " + res.status + " " + (res.url || ""));
            }

            async function ensureOk(url, options) {
                const out = await requestJson(url, options);
                if (!out.res.ok) {
                    throw errorOf(out.res, out.data);
                }
                return out.data;
            }

            function jsonOptions(method, body) {
                const options = { method: method, headers: { 'Content-Type': 'application/json' } };
                if (body !== undefined) {
                    options.body = JSON.stringify(body);
                }
                return options;
            }

            window.Transport = {
                async fetchConfig() {
                    return await ensureOk('/api/config');
                },

                async overview() {
                    return await ensureOk('/api/overview');
                },

                async updateCheck() {
                    return await ensureOk('/api/update');
                },

                async saveConfig(payload) {
                    return await ensureOk('/api/config', jsonOptions('POST', payload));
                },

                async deleteProfile(profileId) {
                    return await ensureOk('/api/profiles/' + encodeURIComponent(profileId), { method: 'DELETE' });
                },

                async resetData(payload) {
                    return await ensureOk('/api/data/reset', jsonOptions('POST', payload));
                },

                // 304 Not Modified resolves to { status: 304, etag: null, data: null }.
                async fetchListings(etag) {
                    const headers = {};
                    if (etag) headers['If-None-Match'] = etag;
                    const out = await requestJson('/api/listings', { headers: headers });
                    if (out.res.status === 304) {
                        return { status: 304, etag: null, data: null };
                    }
                    if (!out.res.ok) {
                        throw errorOf(out.res, out.data);
                    }
                    return { status: out.res.status, etag: out.res.headers.get('ETag'), data: out.data };
                },

                async updateStatus(id, status) {
                    return await ensureOk('/api/listings/' + id + '/status', jsonOptions('PATCH', { status: status }));
                },

                async updateNotes(id, notes) {
                    return await ensureOk('/api/listings/' + id + '/notes', jsonOptions('PATCH', { notes: notes }));
                },

                async updateTags(id, tags) {
                    return await ensureOk('/api/listings/' + id + '/tags', jsonOptions('PATCH', { tags: tags }));
                },

                async geocode(query) {
                    return await ensureOk('/api/geocode', jsonOptions('POST', { query: query }));
                },

                // Resolves to the status payload, or null when the request failed.
                async scrapeStatus() {
                    const out = await requestJson('/api/scrape/status');
                    if (!out.res.ok) return null;
                    return (out.data && typeof out.data === 'object') ? out.data : null;
                },

                // Resolves to { conflict: true } for the 409 "already running" case.
                async startScrape(profileId) {
                    const isSpecific = profileId && profileId !== 'all' && profileId !== 'null';
                    const url = isSpecific ? '/api/scrape?profile=' + encodeURIComponent(profileId) : '/api/scrape';
                    const out = await requestJson(url, jsonOptions('POST', isSpecific ? { profile: profileId } : {}));
                    return { conflict: out.res.status === 409 || (out.data && out.data.status === 'already_running') };
                },

                async cancelScrape() {
                    const out = await requestJson('/api/scrape/cancel', jsonOptions('POST', {}));
                    return { status: out.data ? out.data.status : null };
                },

                async priceHistory(listingId) {
                    return await ensureOk('/api/listings/' + listingId + '/price-history');
                },

                async listingDetail(listingId) {
                    return await ensureOk('/api/listings/' + encodeURIComponent(listingId));
                },

                async airQuality(listingId) {
                    return await ensureOk('/api/listings/' + listingId + '/air-quality');
                },

                async getLlmStatus() {
                    return await ensureOk('/api/llm/status');
                },

                async testLlm(payload) {
                    return await ensureOk('/api/llm/test', jsonOptions('POST', payload || {}));
                },

                async generateAiAudit(listingId) {
                    return await ensureOk('/api/listings/' + encodeURIComponent(listingId) + '/ai-audit', jsonOptions('POST', {}));
                }
            };
        })();
