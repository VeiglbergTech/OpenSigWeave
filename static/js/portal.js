const userData = JSON.parse(document.body.dataset.userData || "{}");
const opensigweaveCsrfToken = document.body.dataset.csrfToken || "";
const originalFetch = window.fetch.bind(window);
let portalSessionRedirecting = false;

function redirectExpiredPortalSession() {
    if (portalSessionRedirecting) return;
    portalSessionRedirecting = true;
    // Replace the stale page in history, so Back cannot reopen it.
    window.location.replace("/login");
}

async function checkPortalSession() {
    if (portalSessionRedirecting) return false;
    try {
        const response = await originalFetch("/api/session/status", {
            credentials: "same-origin",
            cache: "no-store",
            headers: { Accept: "application/json" }
        });
        if (response.status === 401) redirectExpiredPortalSession();
        return response.ok;
    } catch (_error) {
        // A network outage is not proof of logout. Keep navigation blocked.
        return false;
    }
}

window.fetch = async (resource, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
        const headers = new Headers(options.headers || {});
        headers.set("X-CSRF-Token", opensigweaveCsrfToken);
        options = { ...options, headers };
    }
    const response = await originalFetch(resource, options);
    // Some admin endpoints return 403 for missing sessions, while other
    // operations return legitimate 401/403 errors. Check the real session
    // before redirecting; never mistake a permission error for a logout.
    if (response.status === 401 || response.status === 403) {
        void checkPortalSession();
    }
    return response;
};

        var userRichEditor, adminRichEditor, signatureRichEditor;
        let personalEffective = null;
        async function reloadPersonalEffective() {
            const raw = document.getElementById("raw-html-editor");
            if (!raw) return null;
            const response = await fetch("/api/my-signature/effective", {
                credentials: "same-origin", cache: "no-store"
            });
            if (!response.ok) throw new Error("Could not load the effective signature (" + response.status + ").");
            const data = await response.json();
            personalEffective = data;
            raw.value = data.html_content || "";
            if (userRichEditor) userRichEditor.setData(raw.value);
            checkAllLocks();
            return data;
        }
        const notify = (msg, type = 'success') => window.dispatchEvent(new CustomEvent('notify', { detail: { msg, type } }));

        // The HTML source must remain editable if a CKEditor asset or plugin
        // fails. A dead editor with no toolbar must never cause data loss.
        function mountHtmlEditorFallback(host, raw, options, error) {
            console.error("OpenSigWeave: CKEditor unavailable; using HTML source editor.", error);
            const warning = document.createElement("p");
            warning.setAttribute("role", "alert");
            warning.className = "mb-2 text-sm text-amber-300";
            warning.textContent = "Visual editor unavailable. You can still edit the HTML source below.";
            const textarea = document.createElement("textarea");
            textarea.className = "osw-editor-fallback w-full min-h-[180px] rounded border border-amber-700 bg-gray-950 p-3 font-mono text-sm text-green-300";
            textarea.setAttribute("aria-label", "HTML signature source");
            textarea.value = raw.value;
            let onChange = typeof options.onChange === "function" ? options.onChange : null;
            const changed = () => {
                raw.value = textarea.value;
                if (onChange) onChange(textarea.value);
                checkAllLocks();
            };
            textarea.addEventListener("input", changed);
            const adapter = {
                getData: () => textarea.value,
                setData(html) { textarea.value = typeof html === "string" ? html : ""; raw.value = textarea.value; },
                setOnChange(callback) { onChange = typeof callback === "function" ? callback : null; },
                setReadOnly(value) { textarea.readOnly = !!value; },
                focus() { textarea.focus(); },
                saveImageImportSelection() {},
                clearImageImportSelection() {},
                insertText(value) {
                    if (textarea.readOnly) return;
                    textarea.setRangeText(String(value || ""), textarea.selectionStart, textarea.selectionEnd, "end");
                    changed();
                },
                insertImage(url) {
                    const asset = new URL(url, window.location.origin);
                    if (asset.origin !== window.location.origin ||
                        !/^\/signature-assets\/[a-f0-9]{64}\.(?:png|jpg|webp|gif)$/.test(asset.pathname) ||
                        asset.search || asset.hash) throw new Error("Only managed signature images are allowed.");
                    this.insertText('<img src="' + asset.pathname + '" alt="">');
                },
                destroy() { host.replaceChildren(); }
            };
            adapter.setReadOnly(options.readOnly);
            host.replaceChildren(warning, textarea);
            host.__openSigWeaveEditor = adapter;
            if (!raw.dataset.richEditorSyncBound) {
                raw.addEventListener("input", () => adapter.setData(raw.value));
                raw.dataset.richEditorSyncBound = "true";
            }
            return adapter;
        }

        async function mountRichEditor(hostId, rawId, options = {}) {
            const host = document.getElementById(hostId);
            const raw = document.getElementById(rawId);
            if (!host || !raw) return null;
            host.classList.add("osw-rich-editor");
            if (!window.OpenSigWeaveEditor) {
                return mountHtmlEditorFallback(host, raw, options, new Error("CKEditor script not loaded."));
            }
            if (host.__openSigWeaveEditor) {
                host.__openSigWeaveEditor.setOnChange(html => {
                    raw.value = html;
                    if (typeof options.onChange === "function") options.onChange(html);
                    checkAllLocks();
                });
                host.__openSigWeaveEditor.setData(raw.value);
                host.__openSigWeaveEditor.setReadOnly(!!options.readOnly);
                return host.__openSigWeaveEditor;
            }
            const target = host.querySelector("[data-editor-target]") || host;
            let adapter;
            try {
                adapter = await window.OpenSigWeaveEditor.create(target, {
                initialData: raw.value,
                readOnly: !!options.readOnly,
                imageUpload: !!options.imageUpload,
                onImportImageFromUrl: options.onImportImageFromUrl,
                uploadUrl: options.uploadUrl,
                csrfToken: options.csrfToken,
                onChange: html => {
                    raw.value = html;
                    if (typeof options.onChange === "function") options.onChange(html);
                    checkAllLocks();
                }
                });
            } catch (error) {
                return mountHtmlEditorFallback(host, raw, options, error);
            }
            host.__openSigWeaveEditor = adapter;
            if (!raw.dataset.richEditorSyncBound) {
                raw.addEventListener("input", () => {
                    if (host.__openSigWeaveEditor) host.__openSigWeaveEditor.setData(raw.value);
                    checkAllLocks();
                });
                raw.dataset.richEditorSyncBound = "true";
            }
            return adapter;
        }

        async function initSignatureRichEditor(manager) {
            const source = document.getElementById("signature-html-source");
            if (source) source.value = manager.form.html_content || "";
            signatureRichEditor = await mountRichEditor("signature-template-editor", "signature-html-source", {
                imageUpload: true,
                onImportImageFromUrl: () => manager.openImageImportDialog(),
                uploadUrl: "/api/admin/signature-assets",
                csrfToken: opensigweaveCsrfToken,
                readOnly: manager.isViewingHistoricalVersion,
                onChange: html => { manager.form.html_content = html; }
            });
            // CKEditor may normalize stored HTML while loading. Treat its first
            // rendered value as the baseline, not as a user edit.
            manager.loadedEditorHtml = signatureRichEditor ? signatureRichEditor.getData() : manager.form.html_content;
        }

        // THE SMART LOCK ENGINE
        function toggleLock(lockId, rawId) {
            const rawElement = document.getElementById(rawId);
            const lockEl = document.getElementById(lockId);
            if (!rawElement || !lockEl) return;
            const rawVal = rawElement.value.toLowerCase();
            if (rawVal.includes('<table')) lockEl.classList.remove('hidden');
            else lockEl.classList.add('hidden');
        }
        function checkOutlookMarkupWarnings() {
            const editors = [
                ['raw-html-editor', 'outlook-markup-warning'],
                                ['admin-raw-html-editor', 'admin-outlook-markup-warning']
            ];
            const outlookMarkup = /<!--\s*\[if\s+mso\b|<\s*v:[a-z][\w-]*/i;
            editors.forEach(([editorId, warningId]) => {
                const editor = document.getElementById(editorId);
                const warning = document.getElementById(warningId);
                if (editor && warning) {
                    warning.classList.toggle('hidden', !outlookMarkup.test(editor.value));
                }
            });
        }
        function checkAllLocks() {
            toggleLock('user-rich-editor-lock', 'raw-html-editor');
            toggleLock('admin-rich-editor-lock', 'admin-raw-html-editor');
            checkOutlookMarkupWarnings();
        }

        document.addEventListener('DOMContentLoaded', async function() {
            const userRawEditor = document.getElementById('raw-html-editor');
            if (!document.getElementById('user-rich-editor') || !userRawEditor) return;
            userRichEditor = await mountRichEditor('user-rich-editor', 'raw-html-editor', {
                readOnly: document.body.dataset.canEdit !== "true"
            });
            try {
                await reloadPersonalEffective();
            } catch (error) {
                notify(error.message, "error");
            }
        });

        window.addEventListener('sanitize-html', (e) => {
            const { html, target } = e.detail;
            if (!html) return;

            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            // Work on parsed nodes, never attempt to recognize HTML tags with a regex.
            doc.querySelectorAll('script').forEach((node) => node.remove());
            const cleanHtml = doc.body.innerHTML.trim();

            if (target === 'personal') {
                document.getElementById('raw-html-editor').value = cleanHtml;
                if (userRichEditor) userRichEditor.setData(cleanHtml);
            } else if (target === 'admin') {
                document.getElementById('admin-raw-html-editor').value = cleanHtml;
                if (adminRichEditor) adminRichEditor.setData(cleanHtml);
            }
            checkAllLocks();
            notify("HTML imported. Review the source before saving.");
        });

        async function saveUserSignature() {
            const finalHtml = document.getElementById('raw-html-editor').value;
            const res = await fetch('/api/my-signature', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ html_content: finalHtml }) });
            if (res.ok) {
                await reloadPersonalEffective();
                notify("Signature saved.");
            } else notify("Could not save signature.", "error");
        }

        function portalShell() {
    const stateKey = "opensigweave.portal-state.v1";
    const isAdmin = document.body.dataset.isAdmin === "true";
    const allowedAdminTabs = new Set([
        "identity-providers", "domains", "signatures", "signature-groups",
        "rules", "scheduling", "branding", "backups", "import-export", "system"
    ]);
    let saved = {};
    try { saved = JSON.parse(sessionStorage.getItem(stateKey) || "{}"); } catch (_error) {}
    // Keep existing bookmarked URLs and session state valid after merging the two tabs.
    const normalizeAdminTab = section => (section === "imports" || section === "exports")
        ? "import-export" : section;
    const initialUrl = new URL(window.location.href);
    const directSection = normalizeAdminTab(initialUrl.searchParams.get("section"));
    const directView = initialUrl.searchParams.get("view");
    const initialAdminTab = document.body.dataset.mailSetupRequired === "true"
        ? "system"
        : (allowedAdminTabs.has(directSection) ? directSection
            : (allowedAdminTabs.has(normalizeAdminTab(saved.adminTab)) ? normalizeAdminTab(saved.adminTab) : "identity-providers"));
    const initialView = isAdmin && (
        directView === "admin" || (directView !== "personal" && !directView && (
            allowedAdminTabs.has(directSection) || saved.tab === "admin" ||
            document.body.dataset.idpSetupRequired === "true" ||
            document.body.dataset.mailSetupRequired === "true"
        ))
    ) ? "admin" : "personal";
    return {
        tab: initialView,
        adminTab: initialAdminTab,
        saveNavigation(push = true) {
            try { sessionStorage.setItem(stateKey, JSON.stringify({tab: this.tab, adminTab: this.adminTab})); }
            catch (_error) { /* Session storage is optional. */ }
            const url = new URL(window.location.href);
            url.searchParams.set("view", this.tab);
            if (this.tab === "admin") url.searchParams.set("section", this.adminTab);
            else url.searchParams.delete("section");
            const next = url.pathname + url.search + url.hash;
            if (next !== window.location.pathname + window.location.search + window.location.hash) {
                window.history[push ? "pushState" : "replaceState"]({}, "", next);
            }
        },
        init() {
            this.saveNavigation(false);
            window.addEventListener("popstate", async () => {
                if (!(await checkPortalSession())) {
                    this.saveNavigation(false);
                    return;
                }
                const url = new URL(window.location.href);
                const view = url.searchParams.get("view");
                const section = normalizeAdminTab(url.searchParams.get("section"));
                this.adminTab = allowedAdminTabs.has(section) ? section : this.adminTab;
                this.tab = isAdmin && (view === "admin" || (!view && allowedAdminTabs.has(section)))
                    ? "admin" : "personal";
                // Replace legacy URLs without creating an extra history entry.
                this.saveNavigation(false);
                if (this.tab === "admin" && this.adminTab === "system") {
                    window.dispatchEvent(new Event("osw-system-tab-active"));
                }
            });
            // Catch revoked sessions when returning from another tab or bfcache.
            window.addEventListener("pageshow", event => {
                if (event.persisted) void checkPortalSession();
            });
            document.addEventListener("visibilitychange", () => {
                if (!document.hidden) void checkPortalSession();
            });
            window.addEventListener("focus", () => { void checkPortalSession(); });
        },
        async setAdminTab(section) {
            if (!isAdmin || !allowedAdminTabs.has(section)) return;
            if (!(await checkPortalSession())) return;
            this.adminTab = section;
            this.tab = "admin";
            this.saveNavigation();
            if (section === "system") window.dispatchEvent(new Event("osw-system-tab-active"));
        },
        async openPersonal() {
            if (!(await checkPortalSession())) return;
            this.tab = "personal";
            this.saveNavigation();
        },
        async openAdmin() {
            if (!isAdmin || !(await checkPortalSession())) return;
            this.tab = "admin";
            this.saveNavigation();
            if (this.adminTab === "system") window.dispatchEvent(new Event("osw-system-tab-active"));
        },
        emit(name, detail) { window.dispatchEvent(new CustomEvent(name, { detail })); },
        confirmClearUser() { this.emit("open-confirm", { action: "clear-user" }); },
        confirmDomainToggle(domain) { this.emit("open-confirm", { domain, action: "toggle" }); },
        confirmDomainDelete(domain) { this.emit("open-confirm", { domain, action: "delete" }); },
        confirmClearAdmin(targetId) { this.emit("open-confirm", { action: "clear-admin", targetId }); },
        async previewUserSignature() {
            const raw = document.getElementById("raw-html-editor")?.value || "";
            if (personalEffective && raw === (personalEffective.html_content || "")) {
                try {
                    const current = await reloadPersonalEffective();
                    this.emit("open-preview", { html: current.preview_html, rendered: true });
                } catch (error) { notify(error.message, "error"); }
            } else {
                this.emit("open-preview", { html: raw });
            }
        },
        previewAdminSignature(email) { this.emit("open-preview", { html: document.getElementById("admin-raw-html-editor")?.value || "", previewEmail: email }); }
    };
}
function htmlEditorToggle() { return { showHtml: false }; }

function eligibleProfileProvider(provider) {
    return provider && provider.is_enabled && provider.configured &&
        (provider.provider_type === 'ldap' || provider.provider_type === 'authentik');
}
function eligibleLoginProvider(provider) {
    if (!provider || !provider.is_enabled || !provider.configured) return false;
    if (provider.provider_type === 'ldap') return true;
    return (provider.provider_type === 'authentik' || provider.provider_type === 'oidc') &&
        !!(provider.settings && provider.settings.OIDC_DISCOVERY_URL &&
           provider.settings.OIDC_CLIENT_ID && provider.settings.OIDC_REDIRECT_URI &&
           provider.secret_configured);
}
function domainProviderLabel(providers, providerId, kind) {
    if (!providerId) return kind === 'profile' ? 'No provider' : 'Local Sign-in (No SSO)';
    const provider = providers.find(item => item.id === providerId);
    if (!provider) return 'Missing provider (' + providerId + ')';
    const eligible = kind === 'profile' ? eligibleProfileProvider(provider) : eligibleLoginProvider(provider);
    return provider.display_name + (eligible ? '' : ' (Unavailable)');
}
function domainManager() { return {
    domains: [], providers: [],
    providerLabel(providerId, kind) { return domainProviderLabel(this.providers, providerId, kind); },
    async fetchDomains() {
        const [domainsResponse, providersResponse] = await Promise.all([fetch('/domains/'), fetch('/api/admin/identity-providers')]);
        if (domainsResponse.ok) this.domains = await domainsResponse.json();
        else notify('Could not load domains.', 'error');
        if (providersResponse.ok) this.providers = await providersResponse.json();
        else notify('Could not load identity providers.', 'error');
    }
} }
function identityProviderManager() { return {
    providers: [], editing: false,
    form: {id:'', original_id:null, display_name:'', provider_type:'authentik', is_enabled:true, settings:{}},
    async fetchAll() {
        const response = await fetch('/api/admin/identity-providers');
        if (response.ok) { this.providers = await response.json(); }
        else notify('Could not load identity providers.', 'error');
    },
    newProvider() { this.form={id:'',original_id:null,display_name:'',provider_type:'authentik',is_enabled:true,settings:{AUTHENTIK_TIMEOUT:'5'}}; this.editing=true; },
    changeType(event) {
        const providerType = event && event.target && event.target.value ? event.target.value : this.form.provider_type;
        this.form.provider_type = providerType;
        this.form.settings = providerType === 'ldap'
            ? {LDAP_TIMEOUT:'5',LDAP_USER_FILTER:'(&(objectCategory=person)(objectClass=user)(mail={email}))'}
            : (providerType === 'oidc' ? {OIDC_SCOPES:'openid email profile groups'} : {AUTHENTIK_TIMEOUT:'5'});
    },
    editProvider(provider) {
        // The table row is rendered from the latest API result and already contains
        // all non-secret settings. Reusing it avoids a second async fetch and a
        // visible/editable dialog with temporarily empty fields.
        const item = provider;
        if(!item) return;
        const settings={...(item.settings||{})}; if(item.provider_type==='authentik') settings.AUTHENTIK_API_TOKEN=''; if(item.provider_type==='authentik'||item.provider_type==='oidc') settings.OIDC_CLIENT_SECRET=''; if(item.provider_type==='ldap') settings.LDAP_BIND_PASSWORD='';
        this.form={id:item.id,original_id:item.id,display_name:item.display_name,provider_type:item.provider_type,is_enabled:item.is_enabled,settings};
        this.editing=true;
    },
    async saveProvider() {
        // Flush the most recent x-model input event before constructing the PUT/POST
        // body. Otherwise a fast Save click can send the previous field value.
        await this.$nextTick();
        if(!this.form.id.trim() || !this.form.display_name.trim()) { notify('Provider ID and display name are required.','error'); return; }
        const payload={id:this.form.id,display_name:this.form.display_name,provider_type:this.form.provider_type,is_enabled:this.form.is_enabled,settings:{...this.form.settings}};
        const url=this.form.original_id ? '/api/admin/identity-providers/'+encodeURIComponent(this.form.original_id) : '/api/admin/identity-providers';
        const response=await fetch(url,{method:this.form.original_id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(!response.ok) { notify('Could not save identity provider: '+(await response.text()),'error'); return; }
        notify('Identity provider saved.');
        this.editing=false; await this.fetchAll(); window.dispatchEvent(new Event('domain-updated'));
    },
    async testProvider(provider) {
        const response=await fetch('/api/admin/identity-providers/'+encodeURIComponent(provider.id)+'/test',{method:'POST'});
        if(response.ok) notify('Identity provider connection successful.');
        else notify('Connection test failed. Check the configuration and application logs.','error');
    },
    async deleteProvider(provider) {
        if (!await oswAskConfirmation({ title: 'Delete identity provider?', message: 'Delete identity provider ' + provider.display_name + '? Unassign it from all domains first.' })) return;
        const response=await fetch('/api/admin/identity-providers/'+encodeURIComponent(provider.id),{method:'DELETE'});
        if(!response.ok) { notify('Could not delete identity provider: '+(await response.text()),'error'); return; }
        notify('Identity provider deleted.'); await this.fetchAll(); window.dispatchEvent(new Event('domain-updated'));
    }
} }
        function signatureManager() {
            return {
                signatures: [], domains: [], searchQuery: '', statusFilter: 'all', domainFilter: 'all', imageImportUrl: '', imageImportOpen: false, imageImportBusy: false, imageImportController: null, versions: [], selectedVersionId: '', loadedVersionHtml: '', loadedEditorHtml: '', versionRequestToken: 0, editing: false,
                form: { id: null, name: '', domain_ids: [], is_active: true, html_content: '' },
                get currentVersion() { return this.versions.find(version => version.is_current) || this.versions[0] || null; },
                get selectedVersion() { return this.versions.find(version => version.id === this.selectedVersionId) || null; },
                get isViewingHistoricalVersion() {
                    return Boolean(this.currentVersion && this.selectedVersion && this.currentVersion.id !== this.selectedVersion.id);
                },
                get filteredSignatures() {
                    const query = (this.searchQuery || '').trim().toLocaleLowerCase();
                    return this.signatures.filter(signature => {
                        if (this.statusFilter === 'active' && !signature.is_active) return false;
                        if (this.statusFilter === 'inactive' && signature.is_active) return false;
                        const domainIds = (signature.domain_ids || (signature.domain_id == null ? [] : [signature.domain_id])).map(Number);
                        if (this.domainFilter === 'global' && domainIds.length) return false;
                        if (this.domainFilter.startsWith('domain:') &&
                            !domainIds.includes(Number(this.domainFilter.slice(7)))) return false;
                        const matchable = [signature.name, this.domainNames(signature), signature.marker_code || '',
                            this.formatSignatureMarker(signature)].join(' ').toLocaleLowerCase();
                        return !query || matchable.includes(query);
                    });
                },
                get signatureResultLabel() {
                    return 'Showing ' + this.filteredSignatures.length + ' of ' + this.signatures.length + ' signatures';
                },
                signatureToggleLabel(signature) {
                    return (signature.is_active ? 'Disable ' : 'Enable ') + 'signature ' + signature.name;
                },
                async toggleSignatureStatus(signature) {
                    const nextActive = !signature.is_active;
                    if (!await oswAskConfirmation({
                        title: nextActive ? 'Enable signature?' : 'Disable signature?',
                        message: (nextActive ? 'Enable ' : 'Disable ') + 'signature "' + signature.name + '"?'
                    })) return;
                    try {
                        const domainIds = (signature.domain_ids ||
                            (signature.domain_id == null ? [] : [signature.domain_id])).map(Number);
                        const response = await fetch('/api/admin/signatures/' + encodeURIComponent(signature.id), {
                            method: 'PUT', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({ name: signature.name, domain_ids: domainIds, is_active: nextActive })
                        });
                        if (!response.ok) throw new Error(await response.text());
                        await this.fetchAll();
                        window.dispatchEvent(new window.Event('signature-catalog-updated'));
                        notify('Signature status updated.');
                    } catch (error) {
                        notify('Could not update signature status: ' + error.message, 'error');
                    }
                },
                async previewSignature(signature) {
                    try {
                        const response = await fetch('/api/admin/signatures/' +
                            encodeURIComponent(signature.id) + '/preview', { cache: 'no-store' });
                        if (!response.ok) throw new Error(await response.text());
                        const rendered = await response.json();
                        window.dispatchEvent(new CustomEvent('open-preview', {
                            detail: { html: rendered.html || '', rendered: true }
                        }));
                    } catch (error) {
                        notify('Could not preview signature: ' + error.message, 'error');
                    }
                },
                openImageImportDialog() {
                    if (!this.editing || this.isViewingHistoricalVersion || this.imageImportOpen) return;
                    // Save the CKEditor model range for both desktop toolbar and mobile action.
                    // The native toolbar also captures it, but the mobile action is external.
                    if (signatureRichEditor) signatureRichEditor.saveImageImportSelection();
                    this.imageImportUrl = '';
                    this.imageImportOpen = true;
                },
                closeImageImportDialog() {
                    if (this.imageImportController) this.imageImportController.abort();
                    this.imageImportController = null;
                    this.imageImportOpen = false;
                    this.imageImportBusy = false;
                    this.imageImportUrl = '';
                    if (signatureRichEditor) signatureRichEditor.clearImageImportSelection();
                },
                async importImageFromUrl() {
                    if (!this.imageImportOpen || !this.editing || this.isViewingHistoricalVersion || this.imageImportBusy) return;
                    const sourceUrl = (this.imageImportUrl || '').trim();
                    let source;
                    try { source = new URL(sourceUrl); }
                    catch (_) { notify('Enter a valid HTTPS image URL.', 'error'); return; }
                    if (source.protocol !== 'https:' || !source.hostname || source.username || source.password) {
                        notify('Enter a valid HTTPS image URL without credentials.', 'error');
                        return;
                    }
                    if (!signatureRichEditor) {
                        notify('Open the signature editor before importing an image.', 'error');
                        return;
                    }
                    const controller = new AbortController();
                    this.imageImportController = controller;
                    this.imageImportBusy = true;
                    try {
                        const response = await fetch('/api/admin/signature-assets/import-url', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({url: sourceUrl}),
                            signal: controller.signal
                        });
                        const data = await response.json();
                        if (controller.signal.aborted || !this.imageImportOpen || !this.editing || this.isViewingHistoricalVersion) return;
                        if (!response.ok || !data.url) {
                            throw new Error(data.detail || 'The image could not be imported.');
                        }
                        const localImageUrl = new URL(data.url, window.location.origin);
                        if (localImageUrl.origin !== window.location.origin ||
                            !/^\/signature-assets\/[a-f0-9]{64}\.(?:png|jpg|webp|gif)$/.test(localImageUrl.pathname) ||
                            localImageUrl.search || localImageUrl.hash) {
                            throw new Error('The image server returned an invalid asset URL.');
                        }
                        signatureRichEditor.insertImage(localImageUrl.href);
                        this.form.html_content = signatureRichEditor.getData();
                        this.closeImageImportDialog();
                        notify('Image added to the signature.');
                    } catch (error) {
                        if (!controller.signal.aborted) {
                            notify(error && error.message ? error.message : 'Could not import image.', 'error');
                        }
                    } finally {
                        if (this.imageImportController === controller) {
                            this.imageImportController = null;
                            this.imageImportBusy = false;
                        }
                    }
                },
                toggleDomain(domainId, checked) {
                    const id = Number(domainId);
                    this.form.domain_ids = checked ? [...new Set([...this.form.domain_ids.map(Number), id])] : this.form.domain_ids.filter(item => Number(item) !== id);
                },
                domainNames(signature) {
                    const ids = signature.domain_ids || (signature.domain_id == null ? [] : [signature.domain_id]);
                    if (!ids.length) return 'Global';
                    return ids.map(id => { const match = this.domains.find(domain => Number(domain.id) === Number(id)); return match ? match.domain_name : ('Domain #' + id); }).join(', ');
                },
                formatSignatureMarker(signature) {
                    return '<---[' + signature.name + ']--[' + signature.marker_code + ']--->';
                },
                async fetchAll() {
                    try {
                        const [signaturesResponse, domainsResponse] = await Promise.all([
                            fetch('/api/admin/signatures'), fetch('/domains/')
                        ]);
                        if (!signaturesResponse.ok || !domainsResponse.ok) throw new Error('Could not load signature catalog');
                        [this.signatures, this.domains] = await Promise.all([
                            signaturesResponse.json(), domainsResponse.json()
                        ]);
                    } catch (error) { notify(error.message, 'error'); }
                },
                newSignature() {
                    this.closeImageImportDialog();
                    this.form = { id: null, name: '', domain_ids: [], is_active: true, html_content: '' };
                    this.versions = [];
                    this.selectedVersionId = '';
                    this.loadedVersionHtml = '';
                    this.loadedEditorHtml = '';
                    ++this.versionRequestToken;
                    this.editing = true;
                    this.$nextTick(() => initSignatureRichEditor(this));
                },
                async editSignature(signature) {
                    this.closeImageImportDialog();
                    this.form = { id: signature.id, name: signature.name,
                        domain_ids: Array.isArray(signature.domain_ids) ? signature.domain_ids.map(Number) : (signature.domain_id == null ? [] : [Number(signature.domain_id)]),
                        is_active: signature.is_active, html_content: '' };
                    this.versions = [];
                    this.selectedVersionId = '';
                    this.loadedVersionHtml = '';
                    this.loadedEditorHtml = '';
                    ++this.versionRequestToken;
                    this.editing = true;
                    await this.loadVersions(signature.id);
                    this.$nextTick(() => initSignatureRichEditor(this));
                },
                versionLabel(version) {
                    const created = version.created_at ? new Date(version.created_at).toLocaleString() : '';
                    return 'v' + version.version_number + (version.is_current ? ' · Current' : '') + (created ? ' · ' + created : '') + (version.created_by ? ' · ' + version.created_by : '');
                },
                async loadVersions(signatureId) {
                    const response = await fetch('/api/admin/signatures/' + encodeURIComponent(signatureId) + '/versions');
                    if (!response.ok) { notify('Could not load signature versions.', 'error'); return; }
                    const versions = await response.json();
                    if (this.form.id !== signatureId || !this.editing) return;
                    this.versions = versions;
                    if (this.versions.length) {
                        this.selectedVersionId = (this.currentVersion || this.versions[0]).id;
                        await this.loadVersion(this.selectedVersionId);
                    }
                },
                async loadVersion(versionId) {
                    if (!this.form.id || !versionId) return;
                    const signatureId = this.form.id;
                    const requestToken = ++this.versionRequestToken;
                    const response = await fetch('/api/admin/signatures/' + encodeURIComponent(signatureId) + '/versions/' + encodeURIComponent(versionId));
                    if (this.form.id !== signatureId || requestToken !== this.versionRequestToken || !this.editing) return;
                    if (!response.ok) { notify('Could not load signature version.', 'error'); return; }
                    const version = await response.json();
                    if (this.form.id !== signatureId || requestToken !== this.versionRequestToken || !this.editing) return;
                    this.selectedVersionId = version.id;
                    this.loadedVersionHtml = version.html_content || '';
                    this.form.html_content = this.loadedVersionHtml;
                    const source = document.getElementById('signature-html-source');
                    if (source) source.value = this.form.html_content;
                    if (signatureRichEditor) {
                        signatureRichEditor.setData(this.form.html_content);
                        signatureRichEditor.setReadOnly(this.isViewingHistoricalVersion);
                        this.loadedEditorHtml = signatureRichEditor.getData();
                    } else {
                        this.loadedEditorHtml = this.loadedVersionHtml;
                    }
                },
                async restoreSelectedVersion() {
                    if (!this.form.id || !this.selectedVersionId || !this.isViewingHistoricalVersion) return;
                    const response = await fetch(
                        '/api/admin/signatures/' + encodeURIComponent(this.form.id)
                        + '/versions/' + encodeURIComponent(this.selectedVersionId) + '/restore',
                        { method: 'POST' }
                    );
                    if (!response.ok) { notify('Could not restore signature version: ' + (await response.text()), 'error'); return; }
                    notify('Selected version restored as a new current version.');
                    await this.loadVersions(this.form.id);
                    await this.fetchAll();
                },
                async saveSignature() {
                    const editorHtml = signatureRichEditor ? signatureRichEditor.getData() : this.form.html_content;
                    // Never turn a read-only historical preview or CKEditor's load-time
                    // HTML normalization into an accidental new current version.
                    const hasContentChanges = Boolean(this.form.id && !this.isViewingHistoricalVersion
                        && editorHtml !== this.loadedEditorHtml);
                    if (!this.isViewingHistoricalVersion) this.form.html_content = editorHtml;
                    if (!this.form.name.trim()) { notify('Please enter a signature name.', 'error'); return; }
                    const domainIds = [...new Set(this.form.domain_ids.map(Number))];
                    if (!this.form.id && !this.form.html_content.trim()) { notify('HTML template is required.', 'error'); return; }
                    let response;
                    if (!this.form.id) {
                        response = await fetch('/api/admin/signatures', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.form.name, domain_ids: domainIds,
                                is_active: this.form.is_active, html_content: this.form.html_content }) });
                    } else {
                        response = await fetch('/api/admin/signatures/' + encodeURIComponent(this.form.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.form.name, domain_ids: domainIds, is_active: this.form.is_active }) });
                        if (response.ok && hasContentChanges && this.form.html_content.trim()) {
                            response = await fetch('/api/admin/signatures/' + encodeURIComponent(this.form.id) + '/versions',
                                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ html_content: this.form.html_content }) });
                        }
                    }
                    if (!response.ok) { notify('Could not save signature: ' + (await response.text()), 'error'); return; }
                    notify('Signature saved.');
                    this.editing = false;
                    await this.fetchAll();
                    window.dispatchEvent(new window.Event('signature-catalog-updated'));
                }
            };
        }

        function signatureGroupManager() {
            return {
                groups: [], signatures: [], domains: [], editing: false, memberEditor: false, previewPickerOpen: false, previewPickerGroup: null, previewPickerDomainId: '',
                form: { id: null, name: '', slug: '', domain_ids: [], is_active: true },
                memberForm: { group_id: '', signature_id: '', weight: 1 },
                async fetchAll() {
                    try {
                        const responses = await Promise.all([
                            fetch('/api/admin/signature-groups'), fetch('/api/admin/signatures'), fetch('/domains/')
                        ]);
                        if (responses.some(response => !response.ok)) throw new Error('Could not load signature group data');
                        [this.groups, this.signatures, this.domains] = await Promise.all(responses.map(response => response.json()));
                    } catch (error) { notify(error.message, 'error'); }
                },
                signatureName(id) {
                    const item = this.signatures.find(signature => signature.id === id);
                    return item ? item.name : 'Missing signature';
                },
                compactSignatureName(id) {
                    const name = Array.from(this.signatureName(id));
                    return name.length > 3 ? name.slice(0, 3).join('') + '...' : name.join('');
                },
                toggleDomain(id, checked) {
                    const value = Number(id);
                    this.form.domain_ids = checked
                        ? [...new Set([...this.form.domain_ids.map(Number), value])]
                        : this.form.domain_ids.filter(item => Number(item) !== value);
                },
                openMemberEditor(groupId) {
                    this.memberForm = { group_id: groupId ? String(groupId) : '', signature_id: '', weight: 1 };
                    this.memberEditor = true;
                },
                newGroup() {
                    this.form = { id: null, name: '', slug: '', domain_ids: [], is_active: true };
                    this.editing = true;
                },
                editGroup(group) {
                    this.form = { id: group.id, name: group.name, slug: group.slug,
                        domain_ids: (group.domain_ids || []).map(Number), is_active: group.is_active };
                    this.editing = true;
                },
                async saveGroup() {
                    if (!this.form.name.trim()) { notify('Please enter a group name.', 'error'); return; }
                    const payload = { name: this.form.name.trim(), slug: this.form.slug.trim() || null,
                        domain_ids: this.form.domain_ids.map(Number), is_active: this.form.is_active };
                    const response = await fetch(this.form.id
                        ? '/api/admin/signature-groups/' + encodeURIComponent(this.form.id)
                        : '/api/admin/signature-groups', {
                        method: this.form.id ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
                    });
                    if (!response.ok) { notify('Could not save group: ' + (await response.text()), 'error'); return; }
                    this.editing = false; notify('Signature group saved.'); await this.fetchAll();
                    window.dispatchEvent(new window.Event('signature-groups-updated'));
                },
                async addMember() {
                    if (!this.memberForm.group_id || !this.memberForm.signature_id) { notify('Select a group and signature.', 'error'); return; }
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(this.memberForm.group_id) + '/members', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ signature_id: this.memberForm.signature_id, weight: Number(this.memberForm.weight), is_enabled: true })
                    });
                    if (!response.ok) { notify('Could not add signature: ' + (await response.text()), 'error'); return; }
                    this.memberEditor = false;
                    this.memberForm = { group_id: '', signature_id: '', weight: 1 };
                    notify('Signature added to group.'); await this.fetchAll();
                },
                async saveMember(group, member) {
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(group.id) + '/members/' + encodeURIComponent(member.signature_id), {
                        method: 'PUT', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ signature_id: member.signature_id, weight: Number(member.weight), is_enabled: member.is_enabled })
                    });
                    if (!response.ok) notify('Could not update group member: ' + (await response.text()), 'error');
                    else notify('Group member updated.');
                },
                async removeMember(group, member) {
                    if (!await oswAskConfirmation({ title: 'Remove signature?', message: 'Remove this signature from the group?' })) return;
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(group.id) + '/members/' + encodeURIComponent(member.signature_id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not remove group member.', 'error'); return; }
                    await this.fetchAll();
                },
                previewDomainsForGroup(group) {
                    if (!group || !group.is_active) return [];
                    const groupDomainIds = (group.domain_ids || []).map(Number);
                    const enabledMemberIds = new Set((group.members || []).filter(member =>
                        member.is_enabled && Number(member.weight) > 0).map(member => member.signature_id));
                    const activeMembers = this.signatures.filter(signature =>
                        signature.is_active && enabledMemberIds.has(signature.id));
                    return this.domains.filter(domain => {
                        if (!domain.is_active) return false;
                        const id = Number(domain.id);
                        if (groupDomainIds.length && !groupDomainIds.includes(id)) return false;
                        return activeMembers.some(signature => {
                            const ids = signature.domain_ids ||
                                (signature.domain_id == null ? [] : [signature.domain_id]);
                            return !ids.length || ids.map(Number).includes(id);
                        });
                    });
                },
                get previewPickerDomains() { return this.previewDomainsForGroup(this.previewPickerGroup); },
                openGroupPreview(group) {
                    const eligible = this.previewDomainsForGroup(group);
                    if (!eligible.length) {
                        notify('No active domain has an eligible signature for this group.', 'error');
                        return;
                    }
                    if (eligible.length === 1) {
                        void this.previewGroup(group, eligible[0].id);
                        return;
                    }
                    this.previewPickerGroup = group;
                    this.previewPickerDomainId = String(eligible[0].id);
                    this.previewPickerOpen = true;
                },
                async previewSelectedGroup() {
                    if (this.previewPickerGroup && this.previewPickerDomainId) {
                        await this.previewGroup(this.previewPickerGroup, this.previewPickerDomainId);
                    }
                },
                async previewGroup(group, domainId = null) {
                    const eligible = this.previewDomainsForGroup(group);
                    const domain = domainId === null ? eligible[0] :
                        eligible.find(item => Number(item.id) === Number(domainId));
                    if (!domain) {
                        notify('No active domain has an eligible signature for this group.', 'error');
                        return false;
                    }
                    const url = '/api/admin/signature-groups/' + encodeURIComponent(group.id)
                        + '/preview?domain_id=' + encodeURIComponent(domain.id);
                    const response = await fetch(url, { headers: { 'Accept': 'application/json' } });
                    if (!response.ok) { notify('Could not render group preview: ' + (await response.text()), 'error'); return false; }
                    const result = await response.json();
                    if (result.rotation_state_changed !== false) {
                        notify('Group preview did not confirm rotation safety.', 'error');
                        return false;
                    }
                    this.previewPickerOpen = false;
                    window.dispatchEvent(new CustomEvent('open-preview', { detail: { html: result.html, rendered: true } }));
                    notify('Preview for ' + domain.domain_name + ' does not change rotation history.');
                    return true;
                },
                async deleteGroup(group) {
                    if (!await oswAskConfirmation({ title: 'Delete signature group?', message: 'Delete signature group "' + group.name + '"?' })) return;
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(group.id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete group: ' + (await response.text()), 'error'); return; }
                    notify('Group deleted.'); await this.fetchAll();
                    window.dispatchEvent(new window.Event('signature-groups-updated'));
                }
            };
        }

        function ruleManager() {
            return {
                rules: [], legacySchedules: [], domains: [], signatures: [], groups: [], editing: false,
                loadSequence: 0,
                form: { id: null, name: '', priority: 0, action_mode: 'SIGNATURE', signature_id: '', group_id: '', is_enabled: true, conditions: [], time_limited: false, starts_local: '', ends_local: '', legacy_schedule_id: null },
                get combinedRules() {
                    const ordinary = this.rules.map(rule => ({...rule, source: 'rule'}));
                    const schedules = this.legacySchedules.map(item => {
                        const domain = this.domains.find(row => Number(row.id) === Number(item.domain_id));
                        return { id: item.id, source: 'schedule', name: item.name,
                            priority: item.priority, action_mode: item.target_type,
                            signature_id: item.signature_id, group_id: item.group_id,
                            is_enabled: item.is_enabled, starts_at: item.starts_at, ends_at: item.ends_at,
                            conditions: domain ? [{ kind: 'domain_equals', key: '', value: domain.domain_name }] : [] };
                    });
                    return [...ordinary, ...schedules].sort((left, right) =>
                        Number(this.isActiveTimedRule(right)) - Number(this.isActiveTimedRule(left)) ||
                        right.priority - left.priority || left.name.localeCompare(right.name));
                },
                ruleWindow(rule) {
                    if (rule.source === 'schedule') return { start: rule.starts_at, end: rule.ends_at };
                    const condition = (rule.conditions || []).find(item => item.kind === 'time_window');
                    return condition ? { start: condition.key, end: condition.value } : null;
                },
                isActiveTimedRule(rule) {
                    const window = this.ruleWindow(rule);
                    return !!window && Date.parse(window.start) <= Date.now() && Date.now() < Date.parse(window.end);
                },
                formatLocal(value) {
                    const d = new Date(value);
                    return Number.isNaN(d.getTime()) ? 'Invalid date' :
                        new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(d);
                },
                formatWindow(rule) {
                    const window = this.ruleWindow(rule);
                    return window ? this.formatLocal(window.start) + ' – ' + this.formatLocal(window.end) : 'Always active';
                },
                toInputValue(value) {
                    const date = value instanceof Date ? value : new Date(value);
                    return Number.isNaN(date.getTime()) ? '' :
                        new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
                },
                defaultWindow() {
                    const now = new Date();
                    return { starts_local: this.toInputValue(now),
                        ends_local: this.toInputValue(new Date(now.getTime() + 7 * 86400000)) };
                },
                validScheduleLocalTime(value) {
                    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value || '')) return false;
                    const date = new Date(value);
                    if (Number.isNaN(date.getTime()) || this.toInputValue(date) !== value) return false;
                    for (const minutes of [-180,-120,-90,-60,-30,30,60,90,120,180]) {
                        if (this.toInputValue(new Date(date.getTime() + minutes * 60000)) === value) return false;
                    }
                    return true;
                },
                formatConditions(rule) {
                    const names = {
                        sender_equals: 'Sender equals', sender_not_equals: 'Sender not equal to',
                        domain_equals: 'Domain equals', domain_not_equals: 'Domain not equal to',
                        group_contains: 'Group contains',
                        attribute_equals: 'Attribute equals', attribute_not_equals: 'Attribute not equal to',
                        claim_equals: 'Claim equals', claim_not_equals: 'Claim not equal to'
                    };
                    const conditions = (rule.conditions || []).filter(item => item.kind !== 'time_window');
                    return conditions.length
                        ? conditions.map(condition => (names[condition.kind] || condition.kind)
                            + ': ' + (condition.key ? condition.key + ' = ' : '') + condition.value).join(' AND ')
                        : 'Always matches';
                },
                get matchingSignatures() { return this.signatures.filter(item => item.is_active); },
                ruleActionLabel(rule) {
                    if (rule.action_mode === 'GROUP') return 'Signature group';
                    if (rule.action_mode === 'NONE') return 'No signature';
                    if (rule.action_mode === 'SIGNATURE' || rule.signature_id) return 'Signature';
                    return 'Legacy rotation';
                },
                init() {
                    window.addEventListener('signature-groups-updated', () => this.fetchAll());
                    this.fetchAll();
                },
                async fetchAll() {
                    // Several refreshes can overlap when a rule is saved, a section is
                    // reopened, or a signature group changes. A slow, older GET must not
                    // overwrite the data loaded by a newer refresh after the save.
                    const sequence = ++this.loadSequence;
                    try {
                        const responses = await Promise.all([
                            fetch('/api/admin/rules'), fetch('/api/admin/signatures'), fetch('/api/admin/signature-groups'),
                            fetch('/api/admin/schedules'), fetch('/domains/')
                        ]);
                        if (responses.some(response => !response.ok)) throw new Error('Could not load rule data');
                        const data = await Promise.all(responses.map(response => response.json()));
                        if (sequence !== this.loadSequence) return;
                        [this.rules, this.signatures, this.groups, this.legacySchedules, this.domains] = data;
                    } catch (error) {
                        if (sequence === this.loadSequence) notify(error.message, 'error');
                    }
                },
                newRule() {
                    this.form = { id: null, name: '', priority: 0, action_mode: 'SIGNATURE', signature_id: '', group_id: '', is_enabled: true, conditions: [], time_limited: false, legacy_schedule_id: null, ...this.defaultWindow() };
                    this.editing = true;
                },
                editRule(rule) {
                    const window = this.ruleWindow(rule);
                    this.form = {
                        id: rule.source === 'schedule' ? null : rule.id,
                        legacy_schedule_id: rule.source === 'schedule' ? rule.id : null,
                        name: rule.name, priority: rule.priority,
                        action_mode: ['GROUP', 'NONE'].includes(rule.action_mode) ? rule.action_mode : 'SIGNATURE',
                        signature_id: rule.signature_id || '', group_id: rule.group_id || '',
                        is_enabled: rule.is_enabled,
                        conditions: (rule.conditions || []).filter(item => item.kind !== 'time_window').map(item => ({...item})),
                        time_limited: !!window,
                        ...(window ? {starts_local: this.toInputValue(window.start), ends_local: this.toInputValue(window.end)} : this.defaultWindow())
                    };
                    this.editing = true;
                },
                changeAction(event) {
                    // Alpine CSP accepts a method reference and passes the change event.
                    // Read the selected value directly to avoid model update ordering races.
                    if (event && event.target && event.target.value) {
                        this.form.action_mode = event.target.value;
                    }
                    this.form.signature_id = '';
                    this.form.group_id = '';
                },
                addCondition() { this.form.conditions.push({ kind: 'sender_equals', key: '', value: '' }); },
                requiresConditionKey(kind) {
                    return kind === 'attribute_equals' || kind === 'attribute_not_equals'
                        || kind === 'claim_equals' || kind === 'claim_not_equals';
                },
                changeConditionKind(condition, event) {
                    if (event?.target?.value) condition.kind = event.target.value;
                    // Never submit a stale key from a previously selected attribute condition.
                    if (!this.requiresConditionKey(condition.kind)) condition.key = '';
                },
                async saveRule() {
                    if (!this.form.name.trim()) { notify('Please enter a rule name.', 'error'); return; }
                    if (this.form.conditions.some(condition => !String(condition.value || '').trim())) { notify('Every condition needs a value.', 'error'); return; }
                    if (this.form.conditions.some(condition => this.requiresConditionKey(condition.kind) && !String(condition.key || '').trim())) {
                        notify('Attribute and claim conditions require a key.', 'error');
                        return;
                    }
                    if (this.form.action_mode === 'SIGNATURE' && !this.form.signature_id) { notify('Select a signature.', 'error'); return; }
                    if (this.form.action_mode === 'GROUP' && !this.form.group_id) { notify('Select a signature group.', 'error'); return; }
                    const conditions = this.form.conditions.map(condition => ({
                        ...condition, key: this.requiresConditionKey(condition.kind) ? String(condition.key).trim() : ''
                    }));
                    if (this.form.time_limited) {
                        if (!this.validScheduleLocalTime(this.form.starts_local) ||
                            !this.validScheduleLocalTime(this.form.ends_local)) {
                            notify('Choose existing and unambiguous local dates (check daylight saving time).', 'error'); return;
                        }
                        const start = new Date(this.form.starts_local);
                        const end = new Date(this.form.ends_local);
                        if (start >= end) { notify('End must be after start.', 'error'); return; }
                        conditions.push({kind: 'time_window', key: start.toISOString(), value: end.toISOString()});
                    }
                    if (this.form.legacy_schedule_id && !this.form.time_limited) {
                        notify('A converted schedule must retain its time window.', 'error'); return;
                    }
                    const payload = { ...this.form,
                        conditions,
                        signature_id: this.form.action_mode === 'SIGNATURE' ? (this.form.signature_id || null) : null,
                        group_id: this.form.action_mode === 'GROUP' ? (this.form.group_id || null) : null };
                    const url = this.form.legacy_schedule_id
                        ? '/api/admin/schedules/' + encodeURIComponent(this.form.legacy_schedule_id) + '/convert-rule'
                        : this.form.id ? '/api/admin/rules/' + encodeURIComponent(this.form.id) : '/api/admin/rules';
                    const response = await fetch(url, { method: this.form.legacy_schedule_id ? 'POST' : this.form.id ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    if (!response.ok) { notify('Could not save rule: ' + (await response.text()), 'error'); return; }
                    notify('Signature rule saved.'); this.editing = false; await this.fetchAll();
                },
                async deleteRule(rule) {
                    if (!await oswAskConfirmation({ title: 'Delete signature rule?', message: 'Delete rule "' + rule.name + '"?' })) return;
                    const url = rule.source === 'schedule'
                        ? '/api/admin/schedules/' + encodeURIComponent(rule.id)
                        : '/api/admin/rules/' + encodeURIComponent(rule.id);
                    const response = await fetch(url, { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete rule.', 'error'); return; }
                    notify('Signature rule deleted.'); await this.fetchAll();
                }
            };
        }


        function scheduleManager() {
            return {
                schedules: [], signatures: [], groups: [], domains: [], editing: false,
                search: '', statusFilter: 'all', togglingId: null,
                get filteredSchedules() {
                    const query = this.search.trim().toLowerCase();
                    return this.schedules.filter(schedule => {
                        if (this.statusFilter === 'enabled' && !schedule.is_enabled) return false;
                        if (this.statusFilter === 'disabled' && schedule.is_enabled) return false;
                        return !query || [schedule.name, this.targetLabel(schedule), this.domainLabel(schedule.domain_id)]
                            .some(value => String(value || '').toLowerCase().includes(query));
                    });
                },
                form: { id: null, name: '', target_type: 'SIGNATURE', signature_id: '', group_id: '', domain_id: '', starts_local: '', ends_local: '', priority: 0, is_enabled: true },
                init() {
                    window.addEventListener('signature-catalog-updated', () => this.fetchAll());
                    window.addEventListener('signature-groups-updated', () => this.fetchAll());
                    window.addEventListener('domain-updated', () => this.fetchAll());
                    this.fetchAll();
                },
                async fetchAll() {
                    try {
                        const responses = await Promise.all([
                            fetch('/api/admin/schedules'), fetch('/api/admin/signatures'),
                            fetch('/api/admin/signature-groups'), fetch('/domains/')
                        ]);
                        if (responses.some(response => !response.ok)) throw new Error('Could not load scheduling data');
                        [this.schedules, this.signatures, this.groups, this.domains] = await Promise.all(responses.map(response => response.json()));
                    } catch (error) { notify(error.message, 'error'); }
                },
                activeSignatures() { return this.signatures.filter(item => item.is_active); },
                activeGroups() { return this.groups.filter(item => item.is_active); },
                targetLabel(schedule) {
                    if (schedule.target_type === 'GROUP') {
                        const group = this.groups.find(item => item.id === schedule.group_id);
                        return 'Group: ' + (group ? group.name : 'Missing group');
                    }
                    const signature = this.signatures.find(item => item.id === schedule.signature_id);
                    return 'Signature: ' + (signature ? signature.name : 'Missing signature');
                },
                domainLabel(id) {
                    if (id == null) return 'All domains';
                    const domain = this.domains.find(item => Number(item.id) === Number(id));
                    return domain ? domain.domain_name : 'Missing domain';
                },
                toInputValue(value) {
                    const date = value instanceof Date ? value : new Date(value);
                    if (Number.isNaN(date.getTime())) return '';
                    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
                    return local.toISOString().slice(0, 16);
                },
                formatLocal(value) {
                    const date = new Date(value);
                    if (Number.isNaN(date.getTime())) return 'Invalid date';
                    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
                },
                newSchedule() {
                    const start = new Date();
                    start.setSeconds(0, 0);
                    const end = new Date(start.getTime() + 7 * 24 * 60 * 60 * 1000);
                    this.form = { id: null, name: '', target_type: 'SIGNATURE', signature_id: '', group_id: '', domain_id: '',
                        starts_local: this.toInputValue(start), ends_local: this.toInputValue(end), priority: 0, is_enabled: true };
                    this.editing = true;
                },
                editSchedule(schedule) {
                    this.form = { id: schedule.id, name: schedule.name, target_type: schedule.target_type,
                        signature_id: schedule.signature_id || '', group_id: schedule.group_id || '',
                        domain_id: schedule.domain_id == null ? '' : String(schedule.domain_id),
                        starts_local: this.toInputValue(schedule.starts_at), ends_local: this.toInputValue(schedule.ends_at),
                        priority: schedule.priority, is_enabled: schedule.is_enabled };
                    this.editing = true;
                },
                changeTargetType(event) {
                    // Keep the selected target in sync even when Alpine processes change before x-model.
                    if (event && event.target && event.target.value) this.form.target_type = event.target.value;
                    if (this.form.target_type === 'GROUP') this.form.signature_id = '';
                    else this.form.group_id = '';
                },
                validScheduleLocalTime(value) {
                    // Reject nonexistent clock times and the repeated hour at a DST fallback.
                    // datetime-local has no UTC offset, so guessing would silently shift campaigns.
                    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value || '')) return false;
                    const date = new Date(value);
                    if (Number.isNaN(date.getTime()) || this.toInputValue(date) !== value) return false;
                    for (const minutes of [-180, -120, -90, -60, -30, 30, 60, 90, 120, 180]) {
                        if (this.toInputValue(new Date(date.getTime() + minutes * 60000)) === value) return false;
                    }
                    return true;
                },
                async saveSchedule() {
                    if (!this.form.name.trim()) { notify('Please enter a schedule name.', 'error'); return; }
                    if (this.form.target_type === 'SIGNATURE' && !this.form.signature_id) { notify('Select a signature.', 'error'); return; }
                    if (this.form.target_type === 'GROUP' && !this.form.group_id) { notify('Select a signature group.', 'error'); return; }
                    if (!this.validScheduleLocalTime(this.form.starts_local) || !this.validScheduleLocalTime(this.form.ends_local)) {
                        notify('Select an existing, unambiguous local time (check daylight saving time changes).', 'error'); return;
                    }
                    const start = new Date(this.form.starts_local);
                    const end = new Date(this.form.ends_local);
                    if (start >= end) {
                        notify('End time must be after start time.', 'error'); return;
                    }
                    if (!Number.isInteger(Number(this.form.priority)) || Number(this.form.priority) < -100000 || Number(this.form.priority) > 100000) {
                        notify('Priority must be a whole number between -100000 and 100000.', 'error'); return;
                    }
                    const payload = {
                        name: this.form.name.trim(), target_type: this.form.target_type,
                        signature_id: this.form.target_type === 'SIGNATURE' ? this.form.signature_id : null,
                        group_id: this.form.target_type === 'GROUP' ? this.form.group_id : null,
                        domain_id: this.form.domain_id === '' ? null : Number(this.form.domain_id),
                        starts_at: start.toISOString(), ends_at: end.toISOString(),
                        priority: Number(this.form.priority), is_enabled: this.form.is_enabled
                    };
                    if (!await this.persistSchedule(this.form.id, payload)) return;
                    notify('Schedule saved.');
                    this.editing = false;
                    await this.fetchAll();
                },
                async persistSchedule(id, payload) {
                    const url = id ? '/api/admin/schedules/' + encodeURIComponent(id) : '/api/admin/schedules';
                    try {
                        const response = await fetch(url, { method: id ? 'PUT' : 'POST',
                            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                        if (!response.ok) {
                            const problem = await response.json().catch(() => ({}));
                            notify('Could not save schedule: ' + (problem.detail || response.statusText), 'error');
                            return false;
                        }
                        return true;
                    } catch (error) {
                        notify('Could not save schedule: ' + error.message, 'error');
                        return false;
                    }
                },
                async toggleSchedule(schedule) {
                    if (this.togglingId !== null) return;
                    this.togglingId = schedule.id;
                    try {
                        // Preserve raw UTC window and target values: a status change must not
                        // reinterpret a saved time window through the browser time zone.
                        const payload = {
                            name: schedule.name, target_type: schedule.target_type,
                            signature_id: schedule.signature_id, group_id: schedule.group_id,
                            domain_id: schedule.domain_id, starts_at: schedule.starts_at,
                            ends_at: schedule.ends_at, priority: schedule.priority,
                            is_enabled: !schedule.is_enabled
                        };
                        if (!await this.persistSchedule(schedule.id, payload)) return;
                        notify(payload.is_enabled ? 'Schedule enabled.' : 'Schedule disabled.');
                        await this.fetchAll();
                    } finally {
                        this.togglingId = null;
                    }
                },
                async deleteSchedule(schedule) {
                    if (!await oswAskConfirmation({ title: 'Delete schedule?', message: 'Delete schedule "' + schedule.name + '"?' })) return;
                    const response = await fetch('/api/admin/schedules/' + encodeURIComponent(schedule.id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete schedule.', 'error'); return; }
                    notify('Schedule deleted.');
                    await this.fetchAll();
                }
            };
        }

        function brandingManager() {
            return {
                settings: { app_name: 'OpenSigWeave', app_color: '#ff7043', logo_url: '/branding/logo.png', dark_logo_url: '/branding/logo-dark.png', has_custom_dark_logo: false, favicon_url: '/branding/favicon' },
                logoDataUrl: null, darkLogoDataUrl: null, faviconDataUrl: null, logoFileName: '', darkLogoFileName: '', faviconFileName: '', resetLogo: false, resetDarkLogo: false, resetFavicon: false, cacheKey: Date.now(), saving: false,
                async fetchSettings() {
                    try {
                        const response = await fetch('/api/admin/branding', { cache: 'no-store' });
                        if (!response.ok) throw new Error('HTTP ' + response.status);
                        this.settings = await response.json();
                        this.cacheKey = Date.now();
                    } catch (error) {
                        notify('Could not load branding settings: ' + error.message, 'error');
                    }
                },
                logoPreview() {
                    return this.resetLogo ? '/branding/builtin/logo.png' : this.logoDataUrl || this.settings.logo_url + '?v=' + this.cacheKey;
                },
                darkLogoPreview() {
                    if (this.darkLogoDataUrl) return this.darkLogoDataUrl;
                    if (this.resetDarkLogo || !this.settings.has_custom_dark_logo) return this.logoPreview();
                    return this.settings.dark_logo_url + '?v=' + this.cacheKey;
                },
                faviconPreview() {
                    return this.resetFavicon ? '/branding/builtin/favicon' : this.faviconDataUrl || this.settings.favicon_url + '?v=' + this.cacheKey;
                },
                readFile(file, mime) {
                    return new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onerror = () => reject(new Error('Could not read selected file'));
                        reader.onload = () => {
                            const value = String(reader.result || '');
                            const separator = value.indexOf(',');
                            if (!value.startsWith('data:') || separator < 0 || !value.slice(0, separator).endsWith(';base64')) {
                                reject(new Error('Selected file cannot be encoded as an image'));
                                return;
                            }
                            // A .ico file may be reported as application/octet-stream by the OS.
                            // The server still validates the actual ICO structure before accepting it.
                            resolve('data:' + mime + ';base64,' + value.slice(separator + 1));
                        };
                        reader.readAsDataURL(file);
                    });
                },
                imageUploadLimitBytes() { return (this.settings.large_image_uploads ? 12 : 6) * 1024 * 1024; },
                openBrandingPicker(event) {
                    const input = event.currentTarget?.nextElementSibling;
                    if (input?.type === 'file') input.click();
                },
                async chooseLogo(event) {
                    const file = event.target.files && event.target.files[0];
                    if (!file) return;
                    this.logoDataUrl = null;
                    this.logoFileName = '';
                    if (!file.size || file.size > this.imageUploadLimitBytes() || file.type !== 'image/png') {
                        notify('Logo must be a PNG up to ' + (this.settings.large_image_uploads ? 12 : 6) + ' MiB.', 'error');
                        event.target.value = '';
                        return;
                    }
                    try { this.logoDataUrl = await this.readFile(file, 'image/png'); this.logoFileName = file.name + ' · ' + (file.size / 1048576).toFixed(2) + ' MiB'; this.resetLogo = false; }
                    catch (error) { notify(error.message, 'error'); event.target.value = ''; }
                },
                async chooseDarkLogo(event) {
                    const file = event.target.files && event.target.files[0];
                    if (!file) return;
                    this.darkLogoDataUrl = null;
                    this.darkLogoFileName = '';
                    if (!file.size || file.size > this.imageUploadLimitBytes() || file.type !== 'image/png') {
                        notify('Dark mode logo must be a PNG up to ' + (this.settings.large_image_uploads ? 12 : 6) + ' MiB.', 'error');
                        event.target.value = '';
                        return;
                    }
                    try { this.darkLogoDataUrl = await this.readFile(file, 'image/png'); this.darkLogoFileName = file.name + ' · ' + (file.size / 1048576).toFixed(2) + ' MiB'; this.resetDarkLogo = false; }
                    catch (error) { notify(error.message, 'error'); event.target.value = ''; }
                },
                async chooseFavicon(event) {
                    const file = event.target.files && event.target.files[0];
                    if (!file) return;
                    this.faviconDataUrl = null;
                    this.faviconFileName = '';
                    const ico = file.name.toLowerCase().endsWith('.ico');
                    if (!file.size || file.size > this.imageUploadLimitBytes() || (!ico && file.type !== 'image/png')) {
                        notify('Favicon must be PNG or ICO up to ' + (this.settings.large_image_uploads ? 12 : 6) + ' MiB.', 'error');
                        event.target.value = '';
                        return;
                    }
                    try { this.faviconDataUrl = await this.readFile(file, ico ? 'image/x-icon' : 'image/png'); this.faviconFileName = file.name + ' · ' + (file.size / 1048576).toFixed(2) + ' MiB'; this.resetFavicon = false; }
                    catch (error) { notify(error.message, 'error'); event.target.value = ''; }
                },
                restoreLogo() { this.logoDataUrl = null; this.logoFileName = ''; this.resetLogo = true; },
                restoreDarkLogo() { this.darkLogoDataUrl = null; this.darkLogoFileName = ''; this.resetDarkLogo = true; },
                restoreFavicon() { this.faviconDataUrl = null; this.faviconFileName = ''; this.resetFavicon = true; },
                async resetAll() {
                    if (this.saving) return;
                    const confirmed = await oswAskConfirmation({
                        title: 'Reset Branding to Default?',
                        message: 'This will reset the application name, accent color, light and dark mode logos, and favicon to the OpenSigWeave defaults. Domains, signatures, rules, users, and your theme preference will not change.'
                    });
                    if (!confirmed || this.saving) return;
                    this.saving = true;
                    try {
                        const response = await fetch('/api/admin/branding/reset', { method: 'POST' });
                        if (!response.ok) {
                            const problem = await response.json().catch(() => ({}));
                            throw new Error(problem.detail || response.statusText);
                        }
                        const defaults = await response.json();
                        this.settings = defaults;
                        this.logoDataUrl = null;
                        this.darkLogoDataUrl = null;
                        this.faviconDataUrl = null;
                        this.logoFileName = '';
                        this.darkLogoFileName = '';
                        this.faviconFileName = '';
                        this.resetLogo = false;
                        this.resetDarkLogo = false;
                        this.resetFavicon = false;
                        this.cacheKey = Date.now();
                        document.querySelectorAll('[x-data="brandingManager"] input[type="file"]').forEach(input => { input.value = ''; });
                        document.querySelectorAll('.app-brand').forEach(element => { element.textContent = defaults.app_name; });
                        document.title = defaults.app_name + ' Admin';
                        document.querySelectorAll('link[rel="icon"]').forEach(icon => {
                            icon.setAttribute('href', defaults.favicon_url + '?v=' + this.cacheKey);
                        });
                        window.dispatchEvent(new CustomEvent('opensigweave-branding-change', {
                            detail: { app_color: defaults.app_color, version: this.cacheKey }
                        }));
                        notify('Branding reset to default settings.');
                    } catch (error) {
                        notify('Could not reset branding: ' + error.message, 'error');
                    } finally {
                        this.saving = false;
                    }
                },
                async save() {
                    if (this.saving) return;
                    if (!this.settings.app_name || !this.settings.app_name.trim()) { notify('Application name is required.', 'error'); return; }
                    if (!/^#[0-9a-fA-F]{6}$/.test(this.settings.app_color || '')) { notify('Accent color must be a six-digit hex value.', 'error'); return; }
                    const tooBig = [this.logoDataUrl, this.darkLogoDataUrl, this.faviconDataUrl].some(data => data && (data.length - data.indexOf(',') - 1) * .75 > this.imageUploadLimitBytes() + 2);
                    if (tooBig) { notify('A selected image exceeds the currently enabled upload limit.', 'error'); return; }
                    this.saving = true;
                    try {
                        const response = await fetch('/api/admin/branding', {
                            method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                app_name: this.settings.app_name.trim(), app_color: this.settings.app_color,
                                logo_data_url: this.logoDataUrl, dark_logo_data_url: this.darkLogoDataUrl, favicon_data_url: this.faviconDataUrl,
                                reset_logo: this.resetLogo, reset_dark_logo: this.resetDarkLogo, reset_favicon: this.resetFavicon,
                                large_image_uploads: this.settings.large_image_uploads
                            })
                        });
                        if (!response.ok) {
                            const problem = await response.json().catch(() => ({}));
                            throw new Error(problem.detail || response.statusText);
                        }
                        window.location.reload();
                    } catch (error) {
                        notify('Could not save branding: ' + error.message, 'error');
                        this.saving = false;
                    }
                }
            };
        }

        function domainSettingsModal() {
            return {
                show: false, domain: null, allowOverrides: true, injectOnReplies: false, trimWhitespace: true, stripDeviceSignatures: false, modalTab: 'settings', userOverrides: [], newOverrideEmail: '', editingUserEmail: '', editingUserDisabled: false,
                providers: [], identityProviderId: '', authenticationProviderId: '', providerLoading: false, providerError: '', providerRequestId: 0, saving: false,
                fallbackValues: {},
                users: [], userQuery: '', userSource: 'all', userPage: 1, userTotal: 0,
                userLoading: false, userError: '', userWarning: '', userRequestId: 0,
                profileProviders() { return this.providers.filter(eligibleProfileProvider); },
                loginProviders() { return this.providers.filter(eligibleLoginProvider); },
                providerLabel(providerId, kind) { return domainProviderLabel(this.providers, providerId, kind); },
                selectedProfileUnavailable() {
                    return !!this.identityProviderId && !this.profileProviders().some(item => item.id === this.identityProviderId);
                },
                selectedLoginUnavailable() {
                    return !!this.authenticationProviderId && !this.loginProviders().some(item => item.id === this.authenticationProviderId);
                },
                async fetchProviders() {
                    const requestId = ++this.providerRequestId;
                    this.providerLoading = true;
                    this.providerError = '';
                    try {
                        const response = await fetch('/api/admin/identity-providers');
                        if (!response.ok) throw new Error('HTTP ' + response.status);
                        const providers = await response.json();
                        if (requestId === this.providerRequestId) this.providers = providers;
                    } catch (error) {
                        if (requestId === this.providerRequestId) {
                            this.providerError = 'Could not load available providers. Close and reopen Domain Management to retry.';
                            notify(this.providerError, 'error');
                        }
                    } finally {
                        if (requestId === this.providerRequestId) this.providerLoading = false;
                    }
                },
                async open(domainData) {
                    this.show = true;
                    this.domain = domainData;
                    this.allowOverrides = domainData.allow_overrides;
                    this.injectOnReplies = domainData.inject_on_replies;
                    this.trimWhitespace = domainData.trim_whitespace;
                    this.stripDeviceSignatures = domainData.strip_device_signatures;
                    this.identityProviderId = domainData.identity_provider_id || '';
                    this.authenticationProviderId = domainData.authentication_provider_id || '';
                    this.fallbackValues = {
                        first_name: '', last_name: '', title: '', phone: '',
                        company: '', department: '', location: '', mobile: '', website: '',
                        ...(domainData.fallback_values || {})
                    };
                    this.providers = [];
                    this.saving = false;
                    this.modalTab = 'settings';
                    this.editingUserEmail = '';
                    await this.$nextTick();
                    await this.fetchProviders();
                },
                async openUsers() {
                    this.modalTab = 'users';
                    this.userPage = 1;
                    await Promise.all([this.fetchOverrides(), this.fetchUsers()]);
                },
                async fetchUsers() {
                    if (!this.domain) return;
                    const requestId = ++this.userRequestId;
                    this.userLoading = true;
                    this.userError = "";
                    try {
                        const params = new URLSearchParams({
                            q: this.userQuery, source: this.userSource,
                            page: String(this.userPage), page_size: "20"
                        });
                        const response = await fetch("/api/admin/domains/" + this.domain.id + "/users?" + params);
                        const data = await response.json().catch(() => ({}));
                        if (!response.ok) throw new Error(data.detail || "User directory request failed");
                        if (requestId !== this.userRequestId) return;
                        this.users = data.items || [];
                        this.userTotal = data.total || 0;
                        this.userWarning = data.warning || "";
                    } catch (error) {
                        if (requestId !== this.userRequestId) return;
                        this.users = [];
                        this.userTotal = 0;
                        this.userError = "Could not load domain users: " + (error instanceof Error ? error.message : "Unknown error");
                    } finally {
                        if (requestId === this.userRequestId) this.userLoading = false;
                    }
                },
                fetchUsersFirstPage() { this.userPage = 1; this.fetchUsers(); },
                previousUsersPage() { if (this.userPage > 1) { this.userPage--; this.fetchUsers(); } },
                nextUsersPage() { if (this.userPage * 20 < this.userTotal) { this.userPage++; this.fetchUsers(); } },

                async saveSettings() {
                    if (!this.domain || this.saving || this.providerLoading || this.providerError) return;
                    const profileChanged = this.identityProviderId !== (this.domain.identity_provider_id || '');
                    const loginChanged = this.authenticationProviderId !== (this.domain.authentication_provider_id || '');
                    if (profileChanged && this.selectedProfileUnavailable()) {
                        notify('Select an available identity provider or choose no provider.', 'error');
                        return;
                    }
                    if (loginChanged && this.selectedLoginUnavailable()) {
                        notify('Select an available login provider or choose local sign-in.', 'error');
                        return;
                    }
                    // Omit unchanged assignments. This preserves legacy, removed or
                    // disabled provider references while editing unrelated settings.
                    const payload = {
                        is_active: this.domain.is_active,
                        allow_overrides: this.allowOverrides,
                        inject_on_replies: this.injectOnReplies,
                        trim_whitespace: this.trimWhitespace,
                        strip_device_signatures: this.stripDeviceSignatures,
                        fallback_values: { ...this.fallbackValues }
                    };
                    if (profileChanged) payload.identity_provider_id = this.identityProviderId || null;
                    if (loginChanged) payload.authentication_provider_id = this.authenticationProviderId || null;
                    this.saving = true;
                    try {
                        const response = await fetch('/domains/' + this.domain.id, {
                            method: 'PUT', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        if (!response.ok) {
                            const error = await response.json().catch(() => ({}));
                            throw new Error(typeof error.detail === 'string' ? error.detail : 'HTTP ' + response.status);
                        }
                        this.show = false;
                        notify('Domain settings saved.');
                        window.dispatchEvent(new CustomEvent('domain-updated'));
                    } catch (error) {
                        notify('Could not save domain settings: ' + error.message, 'error');
                    } finally {
                        this.saving = false;
                    }
                },
                refreshDomainUsers() { this.fetchOverrides(); this.fetchUsers(); },
                async fetchOverrides() {
                    const res = await fetch(`/api/admin/overrides/${this.domain.domain_name}`);
                    if(res.ok) this.userOverrides = await res.json();
                    else notify("Could not load user overrides.", "error");
                },
                async loadAdminEditor(email, directHtml = null, isDisabled = false) {
                    if(!email || !email.includes('@')) { notify("Please enter a valid email address.", "error"); return; }
                    this.editingUserEmail = email; 
                    
                    if (directHtml !== null) { 
                        document.getElementById('admin-raw-html-editor').value = directHtml;
                        if (adminRichEditor) adminRichEditor.setData(directHtml); 
                        this.editingUserDisabled = isDisabled;
                    } else {
                        const existingUser = this.userOverrides.find(u => u.user_email === email);
                        let content = existingUser ? existingUser.html_content : '';
                        if (!existingUser) {
                            try {
                                const res = await fetch('/api/admin/users/' +
                                    encodeURIComponent(email) + '/effective-signature', { cache: 'no-store' });
                                if (!res.ok) throw new Error('HTTP ' + res.status);
                                content = (await res.json()).html_content || '';
                            } catch (error) {
                                notify('Could not load active rule for this user: ' + error.message, 'error');
                            }
                        }
                        document.getElementById('admin-raw-html-editor').value = content;
                        if (adminRichEditor) adminRichEditor.setData(content);
                        this.editingUserDisabled = existingUser ? existingUser.is_disabled : false;
                    }
                    await this.$nextTick();
                    adminRichEditor = await mountRichEditor('admin-rich-editor', 'admin-raw-html-editor');
                    if (adminRichEditor) adminRichEditor.setData(document.getElementById('admin-raw-html-editor').value);
                    checkAllLocks();
                },
                async saveAdminOverride() {
                    const finalHtml = document.getElementById('admin-raw-html-editor').value;
                    const res = await fetch('/api/admin/overrides', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user_email: this.editingUserEmail, html_content: finalHtml, is_disabled: this.editingUserDisabled }) });
                    if(res.ok) { notify(`User override saved for ${this.editingUserEmail}`); document.getElementById('admin-raw-html-editor').dispatchEvent(new CustomEvent('osw-dialog-saved', { bubbles: true })); this.editingUserEmail = ''; this.fetchOverrides(); this.fetchUsers(); }
                }
            }
        }

        window.addEventListener('submit-new-domain', async (e) => {
            const res = await fetch('/domains/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain_name: e.detail, is_active: true }) });
            if (res.ok) { notify("Domain added."); window.dispatchEvent(new CustomEvent('domain-updated')); } else notify("Could not add domain.", "error");
        });
        window.addEventListener('confirm-action', async (e) => {
            const { domain, action, targetId } = e.detail;
            if (action === 'toggle') {
                const res = await fetch(`/domains/${domain.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: !domain.is_active, allow_overrides: domain.allow_overrides, inject_on_replies: domain.inject_on_replies, trim_whitespace: domain.trim_whitespace, strip_device_signatures: domain.strip_device_signatures }) });
                if (res.ok) { notify("Status updated."); window.dispatchEvent(new CustomEvent('domain-updated')); window.location.reload(); }
            } else if (action === 'delete') {
                const res = await fetch(`/domains/${domain.id}`, { method: 'DELETE' });
                if (res.ok) { notify("Domain deleted."); window.dispatchEvent(new CustomEvent('domain-updated')); window.location.reload(); }
            } else if (action === 'clear-user') {
                const res = await fetch('/api/my-signature', { method: 'DELETE' });
                if(res.ok) {
                    try {
                        await reloadPersonalEffective();
                        notify("Override cleared. The active rule is now shown.");
                    } catch (error) { notify(error.message, "error"); }
                } else { notify("Failed to clear signature.", "error"); }
            } else if (action === 'clear-admin') {
                const res = await fetch(`/api/admin/overrides/${targetId}`, { method: 'DELETE' });
                if(res.ok) { notify("User override deleted."); window.dispatchEvent(new CustomEvent('refresh-admin-overrides')); }
            }
        });


function toastManager() {
    return {
        toasts: [],
        add(event) {
            const detail = event.detail || {};
            this.toasts.push({ id: Date.now(), msg: detail.msg, type: detail.type || 'success' });
            window.setTimeout(() => this.toasts.shift(), 2900);
        }
    };
}

function sanitizerModal() {
    return {
        show: false,
        rawInput: '',
        targetEditor: '',
        open(event) {
            this.show = true;
            this.targetEditor = event.detail;
            this.rawInput = '';
            window.setTimeout(() => this.$refs.sanitizerInput.focus(), 100);
        },
        apply() {
            window.dispatchEvent(new CustomEvent('sanitize-html', {
                detail: { html: this.rawInput, target: this.targetEditor }
            }));
            this.show = false;
        }
    };
}

function previewModal() {
    return {
        show: false,
        requestToken: 0,
        async open(event) {
            this.show = true;
            const token = ++this.requestToken;
            const detail = event.detail || {};
            const frame = document.getElementById('signature-preview-frame');
            if (!frame) return;
            if (detail.rendered === true) {
                frame.srcdoc = String(detail.html || "");
                return;
            }
            // Render with the backend's identity, fallback and group logic.
            // Never interpolate user-controlled HTML with regex in the browser.
            frame.srcdoc = "";
            try {
                const isAdminPreview = Boolean(detail.previewEmail);
                const endpoint = isAdminPreview
                    ? "/api/admin/signature-preview" : "/api/my-signature/preview";
                const body = isAdminPreview
                    ? { user_email: detail.previewEmail, html_content: detail.html || "" }
                    : { html_content: detail.html || "" };
                const response = await fetch(endpoint, {
                    method: "POST", credentials: "same-origin", cache: "no-store",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(body)
                });
                if (!response.ok) throw new Error("HTTP " + response.status);
                const data = await response.json();
                if (token === this.requestToken && this.show) frame.srcdoc = data.html || "";
            } catch (error) {
                notify("Could not render signature preview: " + error.message, "error");
            }
        }
    };
}

function addDomainModal() {
    return {
        show: false,
        domainName: '',
        open() {
            this.show = true;
            this.domainName = '';
            window.setTimeout(() => this.$refs.domainInput.focus(), 100);
        },
        submit() {
            window.dispatchEvent(new CustomEvent('submit-new-domain', { detail: this.domainName }));
            this.show = false;
        }
    };
}

function confirmModal() {
    return {
        show: false, pendingResolver: null,
        close() {
            this.show = false;
            const resolve = this.pendingResolver;
            this.pendingResolver = null;
            if (resolve) resolve(false);
        },
        domain: null,
        action: '',
        title: '',
        message: '',
        targetId: null,
        open(event) {
            const detail = event.detail || {};
            this.show = true;
            this.action = detail.action;
            this.domain = detail.domain || null;
            this.targetId = detail.targetId || null;
            if (this.action === 'resolve') {
                this.title = String(detail.title || 'Please confirm');
                this.message = String(detail.message || '');
                this.pendingResolver = typeof detail.resolve === 'function' ? detail.resolve : null;
                return;
            }
            if (this.action === 'toggle') {
                this.title = (this.domain.is_active ? 'Disable' : 'Enable') + ' Domain?';
                this.message = 'Are you sure you want to ' + (this.domain.is_active ? 'disable' : 'enable') + ' ' + this.domain.domain_name + '?';
            } else if (this.action === 'delete') {
                this.title = 'Delete Domain?';
                this.message = 'CRITICAL WARNING: Delete ' + this.domain.domain_name + ' permanently?';
            } else if (this.action === 'clear-user') {
                this.title = 'Clear Custom Signature?';
                this.message = "Are you sure you want to clear your custom signature? Your emails will revert to the domain's default template.";
            } else if (this.action === 'clear-admin') {
                this.title = 'Clear User Override?';
                this.message = "Are you sure you want to permanently delete this user's custom signature?";
            }
        },
        confirm() {
            if (this.action === 'resolve') {
                const resolve = this.pendingResolver;
                this.pendingResolver = null;
                this.show = false;
                if (resolve) resolve(true);
                return;
            }
            window.dispatchEvent(new CustomEvent('confirm-action', {
                detail: { domain: this.domain, action: this.action, targetId: this.targetId }
            }));
            this.show = false;
        }
    };
}

function adminExportManager() {
    return {
        selected: ["signatures", "domains", "rules", "groups", "identity_providers"],
        busy: false,
        error: "",
        async download() {
            if (this.busy || this.selected.length === 0) return;
            this.busy = true;
            this.error = "";
            try {
                const query = new URLSearchParams({ components: this.selected.join(",") });
                const response = await fetch("/api/admin/exports?" + query.toString(), {
                    headers: { "Accept": "application/zip" },
                    credentials: "same-origin"
                });
                if (!response.ok) {
                    const problem = await response.json().catch(() => ({}));
                    throw new Error(problem.detail || "Export could not be generated.");
                }
                const blob = await response.blob();
                const objectUrl = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = objectUrl;
                link.download = "opensigweave-component-export.zip";
                link.hidden = true;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(objectUrl);
            } catch (error) {
                this.error = error instanceof Error ? error.message : "Export failed.";
            } finally {
                this.busy = false;
            }
        }
    };
}

function adminImportManager() {
    return {
        file: null,
        fileName: "",
        fileSize: "",
        busy: false,
        error: "",
        hasPreview: false,
        preview: { can_apply: false, components: [], additions: {}, reusable: {}, conflicts: [], excluded: [] },
        importComplete: false,
        resultComponents: "",
        openFilePicker() {
            if (!this.busy) document.getElementById("admin-import-file")?.click();
        },
        componentLabels: {
            signatures: "Signatures",
            domains: "Domains",
            groups: "Signature groups",
            rules: "Rules",
            identity_providers: "Identity providers"
        },
        selectFile(event) {
            this.file = event.target.files && event.target.files[0] ? event.target.files[0] : null;
            this.fileName = this.file ? this.file.name : "";
            this.fileSize = this.file ? (this.file.size / 1024 / 1024).toFixed(2) : "";
            this.hasPreview = false;
            this.preview = { can_apply: false, components: [], additions: {}, reusable: {}, conflicts: [], excluded: [] };
            this.importComplete = false;
            this.resultComponents = "";
            this.error = "";
        },
        async submit(path) {
            if (!this.file) throw new Error("Select an OpenSigWeave ZIP export first.");
            if (this.file.size > 20 * 1024 * 1024) throw new Error("The archive exceeds the 20 MiB limit.");
            const response = await fetch(path, {
                method: "POST",
                headers: { "Content-Type": "application/zip", "Accept": "application/json" },
                credentials: "same-origin",
                body: await this.file.arrayBuffer()
            });
            const result = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(result.detail || "Import request failed.");
            return result;
        },
        async previewImport() {
            if (this.busy || !this.file) return;
            this.busy = true;
            this.error = "";
            this.importComplete = false;
            try {
                this.preview = await this.submit("/api/admin/imports/preview");
                this.hasPreview = true;
            } catch (error) {
                this.hasPreview = false;
                this.error = error instanceof Error ? error.message : "Import preview failed.";
            } finally {
                this.busy = false;
            }
        },
        async applyImport() {
            if (this.busy || !this.file || !this.hasPreview || !this.preview.can_apply) return;
            if (!await oswAskConfirmation({ title: 'Import configuration?', message: 'Import the reviewed configuration now? Existing records will never be overwritten.' })) return;
            this.busy = true;
            this.error = "";
            try {
                const result = await this.submit("/api/admin/imports/apply");
                this.resultComponents = result.components.join(", ");
                this.importComplete = true;
                this.hasPreview = false;
                this.file = null;
                this.fileName = "";
                this.fileSize = "";
                const input = document.getElementById("admin-import-file");
                if (input) input.value = "";
                notify("Configuration import completed.", "success");
            } catch (error) {
                this.error = error instanceof Error ? error.message : "Import failed.";
            } finally {
                this.busy = false;
            }
        }
    };
}


function backupManager() {
    const endpoint = "/api/admin/backups";
    return {
        busy: false, loading: false, error: "", message: "",
        locations: [], locationModal: false, locationSaving: false, locationTesting: "",
        locationEditingId: null, locationTestResult: "",
        locationForm: { name: "", type: "local", enabled: true, server: "", share: "",
                        directory: "", username: "", password: "", domain: "" },
        locationDescription(item) {
            if (item.type === "local") return item.local_path;
            const path = item.directory ? "\\" + item.directory.replaceAll("/", "\\") : "";
            return "\\\\" + item.server + "\\" + item.share + path;
        },
        async fetchLocations() {
            try {
                const response = await fetch(endpoint + "/locations", { credentials: "same-origin", cache: "no-store" });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Could not load backup locations.");
                this.locations = data;
            } catch (error) {
                this.error = error.message || "Could not load backup locations.";
            }
        },
        openLocation(item = null) {
            this.locationEditingId = item ? item.id : null;
            this.locationForm = item
                ? { name: item.name, type: item.type, enabled: item.enabled,
                    server: item.server || "", share: item.share || "",
                    directory: item.directory || "", username: item.username || "",
                    password: "", domain: item.domain || "" }
                : { name: "", type: "local", enabled: true, server: "",
                    share: "", directory: "", username: "", password: "", domain: "" };
            this.locationTestResult = "";
            this.locationModal = true;
        },
        closeLocation() {
            if (this.locationSaving) return;
            this.locationModal = false;
            this.locationForm.password = "";
        },
        async saveLocation() {
            if (this.locationSaving) return;
            this.locationSaving = true;
            this.error = "";
            try {
                const path = this.locationEditingId ? "/locations/" + encodeURIComponent(this.locationEditingId) : "/locations";
                const response = await fetch(endpoint + path, {
                    method: this.locationEditingId ? "PUT" : "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(this.locationForm)
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save backup location.");
                this.locationModal = false;
                this.locationForm.password = "";
                await this.fetchLocations();
                notify("Backup location saved.");
            } catch (error) {
                this.error = error.message || "Could not save backup location.";
            } finally {
                this.locationSaving = false;
            }
        },
        async toggleLocation(item) {
            const response = await fetch(endpoint + "/locations/" + encodeURIComponent(item.id), {
                method: "PUT", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: item.name, type: item.type, enabled: !item.enabled,
                    server: item.server, share: item.share, directory: item.directory,
                    username: item.username, domain: item.domain, password: "" })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) { this.error = typeof data.detail === "string" ? data.detail : "Could not update status."; return; }
            await this.fetchLocations();
        },
        async deleteLocation(item) {
            if (!await oswAskConfirmation({
                title: "Delete backup location?",
                message: "Delete " + item.name + " from the location registry? Existing backup files will not be removed."
            })) return;
            const response = await fetch(endpoint + "/locations/" + encodeURIComponent(item.id), {
                method: "DELETE", credentials: "same-origin"
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) { this.error = typeof data.detail === "string" ? data.detail : "Could not delete backup location."; return; }
            await this.fetchLocations();
            notify("Backup location deleted.");
        },
        async testLocation(item) {
            if (this.locationTesting) return;
            this.locationTesting = item.id;
            this.locationTestResult = "";
            try {
                const response = await fetch(endpoint + "/locations/" + encodeURIComponent(item.id) + "/test", {
                    method: "POST", credentials: "same-origin"
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Connection test failed");
                this.locationTestResult = item.name + ": " + data.detail;
                notify("Backup location connection test succeeded.");
            } catch (error) {
                this.locationTestResult = item.name + ": " + (error.message || "Connection test failed");
                notify(this.locationTestResult, "error");
            } finally {
                this.locationTesting = "";
            }
        },


        rules: [], history: [], ruleModal: false, ruleEditingId: null, ruleSaving: false,
        ruleFilter: "", historyStatus: "", historyRule: "", historyDetail: null,
        ruleForm: { name: "", description: "", enabled: true, location_id: "", content: "full",
            schedule_kind: "daily", hour: 2, minute: 0, weekday: 0, monthday: 1,
            interval_minutes: 1440, timezone: "UTC", retention_count: 14, retention_days: null },
        init() {
            this.fetchAll();
            this.startPolling();
        },
        get hasEnabledLocations() {
            return this.locations.some(item => item.enabled);
        },
        get canDownloadHistory() {
            return !!this.historyDetail && this.historyDetail.status === "success" && !!this.historyDetail.is_retained;
        },
        get hasHistoryError() {
            return !!this.historyDetail && !!this.historyDetail.error_detail;
        },
        get historyDownloadUrl() {
            return this.canDownloadHistory ? endpoint + "/history/" + encodeURIComponent(this.historyDetail.id) + "/download" : "#";
        },
        detailValue(key) {
            const value = this.historyDetail && this.historyDetail[key];
            return value === undefined || value === null || value === "" ? "—" : value;
        },
        detailDate(key) {
            const value = this.historyDetail && this.historyDetail[key];
            return value ? this.formatDate(value) : "—";
        },
        detailDuration() {
            return this.historyDetail && this.historyDetail.duration_seconds !== null ?
                this.historyDetail.duration_seconds + " s" : "—";
        },
        detailSize() {
            return this.formatSize(this.historyDetail && this.historyDetail.size_bytes);
        },
        detailRetained() {
            return this.historyDetail && this.historyDetail.is_retained ? "Yes" : "No";
        },
        get filteredRules() {
            const text = this.ruleFilter.trim().toLowerCase();
            return this.rules.filter(item => !text || (item.name + " " + item.location_name).toLowerCase().includes(text));
        },
        ruleRetentionText(item) {
            const limits = [];
            if (item.retention_count !== null && item.retention_count !== undefined) {
                limits.push("Keep " + item.retention_count);
            }
            if (item.retention_days !== null && item.retention_days !== undefined) {
                limits.push("Max " + item.retention_days + " days");
            }
            return limits.join(" · ") || "—";
        },
        get filteredHistory() {
            return this.history.filter(item => (!this.historyStatus || item.status === this.historyStatus)
                && (!this.historyRule || item.rule_id === this.historyRule));
        },
        async fetchRules() {
            try {
                const response = await fetch(endpoint + "/rules", { credentials: "same-origin", cache: "no-store" });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Could not load backup rules.");
                this.rules = Array.isArray(data) ? data : [];
            } catch (error) {
                this.error = error.message || "Could not load backup rules.";
            }
        },
        async fetchHistory() {
            try {
                const response = await fetch(endpoint + "/history?limit=200", { credentials: "same-origin", cache: "no-store" });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Could not load backup history.");
                this.history = Array.isArray(data) ? data : [];
            } catch (error) {
                this.error = error.message || "Could not load backup history.";
            }
        },
        defaultRule() {
            return { name: "", description: "", enabled: true,
                location_id: this.locations.find(item => item.enabled)?.id || "",
                content: "full", schedule_kind: "daily", hour: 2, minute: 0, weekday: 0,
                monthday: 1, interval_minutes: 1440, timezone: "UTC",
                retention_count: 14, retention_days: null };
        },
        openRule(item = null) {
            this.error = "";
            this.ruleEditingId = item?.id || null;
            this.ruleForm = item ? { ...this.defaultRule(), ...item } : this.defaultRule();
            this.ruleModal = true;
        },
        closeRule() {
            if (!this.ruleSaving) this.ruleModal = false;
        },
        rulePayload(item) {
            return {
                name: item.name, description: item.description || "", enabled: !!item.enabled,
                location_id: item.location_id, content: item.content, schedule_kind: item.schedule_kind,
                hour: Number(item.hour), minute: Number(item.minute), weekday: Number(item.weekday),
                monthday: Number(item.monthday), interval_minutes: Number(item.interval_minutes),
                timezone: "UTC",
                retention_count: item.retention_count === null || item.retention_count === "" ? null : Number(item.retention_count),
                retention_days: item.retention_days === null || item.retention_days === "" ? null : Number(item.retention_days)
            };
        },
        async saveRule() {
            if (this.ruleSaving) return;
            this.ruleSaving = true;
            this.error = "";
            try {
                const path = this.ruleEditingId ? "/rules/" + encodeURIComponent(this.ruleEditingId) : "/rules";
                const response = await fetch(endpoint + path, {
                    method: this.ruleEditingId ? "PUT" : "POST", credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(this.rulePayload(this.ruleForm))
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Could not save backup rule.");
                this.ruleModal = false;
                await this.fetchRules();
                notify("Backup rule saved.");
            } catch (error) {
                this.error = error.message || "Could not save backup rule.";
            } finally {
                this.ruleSaving = false;
            }
        },
        async toggleRule(item) {
            const response = await fetch(endpoint + "/rules/" + encodeURIComponent(item.id), {
                method: "PUT", credentials: "same-origin",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(this.rulePayload({ ...item, enabled: !item.enabled }))
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) { this.error = data.detail || "Could not update backup rule."; return; }
            await this.fetchRules();
        },
        async deleteRule(item) {
            if (!await oswAskConfirmation({
                title: "Delete backup rule?",
                message: "Delete " + item.name + "? Run history and existing encrypted backups are preserved."
            })) return;
            const response = await fetch(endpoint + "/rules/" + encodeURIComponent(item.id), {
                method: "DELETE", credentials: "same-origin"
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) { this.error = data.detail || "Could not delete backup rule."; return; }
            await this.fetchRules();
            await this.fetchHistory();
            notify("Backup rule deleted.");
        },
        async runRule(item) {
            const result = await this.action("/rules/" + encodeURIComponent(item.id) + "/run");
            await this.fetchHistory();
            if (result) {
                if (result.status === "failed") {
                    this.error = result.error_detail || "Could not start backup.";
                    notify(this.error, "error");
                } else {
                    notify("Backup queued. The application may briefly disconnect during the snapshot.");
                }
            }
        },
        openHistoryDetails(item) {
            this.historyDetail = item;
        },
        startPolling() {
            this.backupPoll = setInterval(() => {
                if (!this.loading && !this.busy && !this.ruleModal && !this.locationModal) {
                    this.fetchRules();
                    this.fetchHistory();
                }
            }, 20000);
        },
        destroy() {
            if (this.backupPoll) clearInterval(this.backupPoll);
        },

        async fetchAll() {
            if (this.loading) return;
            this.loading = true;
            this.error = "";
            try {
                await this.fetchLocations();
                await this.fetchRules();
                await this.fetchHistory();
            } finally {
                this.loading = false;
            }
        },
        async action(path, method = "POST", body = null) {
            if (this.busy) return null;
            this.busy = true;
            this.error = "";
            this.message = "";
            try {
                const response = await fetch(endpoint + path, {
                    method, credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: body === null ? undefined : JSON.stringify(body)
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Backup operation failed.");
                await this.fetchAll();
                return data;
            } catch (error) {
                this.error = error instanceof Error ? error.message : "Backup operation failed.";
                return null;
            } finally {
                this.busy = false;
            }
        },
        formatSize(bytes) {
            return typeof bytes === "number" ? (bytes / 1024 / 1024).toFixed(2) + " MiB" : "—";
        },
        formatDate(value) {
            const date = new Date(value);
            return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
        }
    };
}


function systemStatusManager() {
    const labels = {
        domains: "Domains", identity_providers: "Identity providers",
        signatures: "Signatures", groups: "Groups",
        rules: "Rules", schedules: "Schedules", overrides: "User overrides"
    };
    return {
        loading: false, error: "", state: "Not checked",
        checkedAt: "—", database: "Unknown", storage: "Unknown",
        assets: "Unknown", localAdmin: "Unknown", managementApi: "Unknown",
        alertStatus: "Not configured", alertPending: 0, alertActive: 0,
        alertLastUpdate: "—", alertIncidents: [],
        records: [],
        loadingAdapterSettings: false, savingAdapterSettings: false,
        adapterSettingsError: "", adapterSetupCompleted: false,
        mailServer: "unconfigured", monitoringEnabled: false,
        monitoringInterval: 300, controllerUrl: "",
        adapterMonitorStatus: "Not checked", adapterMonitorDetail: "",
        adapterMonitorCheckedAt: "—", adapterChecking: false,
        adapterInstructions: {},
        setAdapterMonitorState(adapter) {
            const statuses = {
                configured: "Registered (not a mail test)", missing: "Modules missing",
                auth_required: "Authentication required", unknown: "Unknown",
                unsupported: "Not supported", unconfigured: "Not configured",
                not_checked: "Not checked"
            };
            const lastCheck = adapter.last_monitored_at ? new Date(adapter.last_monitored_at) : null;
            const interval = Math.max(30, Number(adapter.monitoring_interval) || 300);
            const stale = Boolean(adapter.monitoring_enabled && lastCheck &&
                Date.now() - lastCheck.getTime() > (interval * 2 + 15) * 1000);
            this.adapterMonitorStatus = stale ? "Outdated" :
                (statuses[adapter.last_monitor_status] || "Not checked");
            this.adapterMonitorDetail = stale ? "The latest adapter check is too old." :
                (adapter.last_monitor_detail || "");
            this.adapterMonitorCheckedAt = lastCheck && !Number.isNaN(lastCheck.getTime())
                ? lastCheck.toLocaleString() : "Never";
        },
        statusPoll: null, activationListener: null, visibilityListener: null,
        isPanelVisible() {
            const panel = document.getElementById("osw-system-status-panel");
            return !!panel && panel.getClientRects().length > 0;
        },
        refreshIfVisible() {
            if (document.visibilityState === "visible" && this.isPanelVisible()) this.refresh();
        },
        start() {
            void this.loadAdapterSettings();
            // The initial x-init runs even when Alpine x-show hides the system tab.
            // Wait for the first render and only poll while it is actually visible.
            this.activationListener = () => this.refresh();
            this.visibilityListener = () => this.refreshIfVisible();
            window.addEventListener("osw-system-tab-active", this.activationListener);
            document.addEventListener("visibilitychange", this.visibilityListener);
            this.statusPoll = window.setInterval(() => this.refreshIfVisible(), 10000);
            requestAnimationFrame(() => this.refreshIfVisible());
        },
        destroy() {
            if (this.statusPoll !== null) window.clearInterval(this.statusPoll);
            if (this.activationListener) window.removeEventListener("osw-system-tab-active", this.activationListener);
            if (this.visibilityListener) document.removeEventListener("visibilitychange", this.visibilityListener);
        },
        async loadAdapterSettings() {
            if (this.loadingAdapterSettings) return;
            this.loadingAdapterSettings = true;
            try {
                const response = await fetch("/api/admin/mail-adapter-setup", {
                    headers: { Accept: "application/json" },
                    credentials: "same-origin", cache: "no-store"
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Failed to fetch adapter configuration");
                this.mailServer = data.mail_server;
                this.monitoringEnabled = !!data.monitoring_enabled;
                this.monitoringInterval = data.monitoring_interval;
                this.controllerUrl = data.controller_url || "";
                this.adapterSetupCompleted = !!data.setup_completed;
                this.setAdapterMonitorState(data);
                this.adapterInstructions = data.instructions || {};
                this.adapterSettingsError = "";
            } catch (error) {
                this.adapterSettingsError = error instanceof Error ? error.message : "Adapter settings unavailable";
            } finally {
                this.loadingAdapterSettings = false;
            }
        },
        async saveAdapterSettings() {
            if (this.savingAdapterSettings) return;
            this.savingAdapterSettings = true;
            try {
                const response = await fetch("/api/admin/mail-adapter-setup", {
                    method: "PUT", headers: { "Content-Type": "application/json" },
                    credentials: "same-origin",
                    body: JSON.stringify({
                        mail_server: this.mailServer,
                        monitoring_enabled: this.monitoringEnabled,
                        monitoring_interval: Number(this.monitoringInterval),
                        controller_url: this.controllerUrl
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Invalid settings");
                this.adapterSetupCompleted = !!data.setup_completed;
                this.adapterInstructions = data.instructions || {};
                this.setAdapterMonitorState(data);
                this.adapterSettingsError = "";
                notify("Mail-server settings saved.");
            } catch (error) {
                this.adapterSettingsError = error instanceof Error ? error.message : "Failed to save settings";
                notify("Mail-server settings were not saved.", "error");
            } finally {
                this.savingAdapterSettings = false;
            }
        },
        async checkAdapterNow() {
            if (this.adapterChecking || this.savingAdapterSettings) return;
            this.adapterChecking = true;
            this.adapterSettingsError = "";
            try {
                const response = await fetch("/api/admin/mail-adapter-check", {
                    method: "POST", credentials: "same-origin", cache: "no-store",
                    headers: { Accept: "application/json" }
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(typeof data.detail === "string" ?
                    data.detail : "Adapter health check failed");
                this.setAdapterMonitorState(data);
                notify("Adapter check completed.");
                void this.refresh();
            } catch (error) {
                this.adapterSettingsError = error instanceof Error ? error.message : "Adapter check failed";
                notify("Adapter check failed.", "error");
            } finally {
                this.adapterChecking = false;
            }
        },
        async copyAdapterCommands() {
            if (!this.adapterInstructions.commands) return;
            try {
                await navigator.clipboard.writeText(this.adapterInstructions.commands);
                notify("Server instructions copied.");
            } catch (_error) {
                notify("Clipboard unavailable; select and copy the commands manually.", "error");
            }
        },
        async refresh() {
            if (this.loading) return;
            this.loading = true;
            this.error = "";
            try {
                const response = await fetch("/api/admin/system-status", {
                    headers: { Accept: "application/json" },
                    credentials: "same-origin", cache: "no-store"
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || "Could not load system status.");
                this.state = data.status === "healthy" ? "Healthy" : "Degraded";
                this.checkedAt = data.checked_at ? new Date(data.checked_at).toLocaleString() : "Unknown";
                this.database = data.database?.status === "ok" ? "OK" : "Unavailable";
                this.storage = data.storage?.signature_files === true ? "Accessible" : "Unavailable";
                this.assets = data.storage?.image_assets === true ? "Accessible" : "Unavailable";
                this.localAdmin = data.configuration?.local_admin_enabled ? "Configured" : "Unavailable";
                this.managementApi = data.configuration?.management_api_enabled ? "Enabled" : "Disabled";
                const alerts = data.support_alerts || {};
                this.alertStatus = ({
                    ok: "Healthy", active: "Active failures", stale: "Status outdated",
                    unavailable: "Status unavailable", not_configured: "Not configured"
                })[alerts.status] || "Unknown";
                this.alertPending = Number.isInteger(alerts.pending_count) ? alerts.pending_count : 0;
                this.alertActive = Number.isInteger(alerts.active_count) ? alerts.active_count : 0;
                this.alertLastUpdate = Number.isInteger(alerts.generated_at)
                    ? new Date(alerts.generated_at * 1000).toLocaleString() : "—";
                this.alertIncidents = Array.isArray(alerts.incidents) ? alerts.incidents : [];
                const adapter = data.mail_adapter || {};
                this.setAdapterMonitorState(adapter);
                this.records = Object.entries(data.counts || {}).filter(([key]) => key !== "versions").map(([key, count]) => ({
                    key, label: labels[key] || key, count
                }));
            } catch (error) {
                this.error = "Could not load system status: " + (error instanceof Error ? error.message : "Unknown error");
                this.state = "Unavailable";
                this.checkedAt = new Date().toLocaleString();
                this.database = "Unknown";
                this.storage = "Unknown";
                this.assets = "Unknown";
                this.localAdmin = "Unknown";
                this.managementApi = "Unknown";
                this.alertStatus = "Unavailable";
                this.alertPending = 0;
                this.alertActive = 0;
                this.alertIncidents = [];
                this.alertLastUpdate = "—";
                this.records = [];
            } finally {
                this.loading = false;
            }
        }
    };
}

document.addEventListener("alpine:init", () => {
    Alpine.data("portalShell", portalShell);
    Alpine.data("htmlEditorToggle", htmlEditorToggle);
    Alpine.data("domainManager", domainManager);
    Alpine.data("identityProviderManager", identityProviderManager);
    Alpine.data("domainSettingsModal", domainSettingsModal);
    Alpine.data("signatureManager", signatureManager);
    Alpine.data("signatureGroupManager", signatureGroupManager);
    Alpine.data("ruleManager", ruleManager);
    Alpine.data("scheduleManager", scheduleManager);
    Alpine.data("brandingManager", brandingManager);
    Alpine.data("adminExportManager", adminExportManager);
    Alpine.data("adminImportManager", adminImportManager);
    Alpine.data("backupManager", backupManager);
    Alpine.data("systemStatusManager", systemStatusManager);
    Alpine.data("toastManager", toastManager);
    Alpine.data("sanitizerModal", sanitizerModal);
    Alpine.data("previewModal", previewModal);
    Alpine.data("addDomainModal", addDomainModal);
    Alpine.data("confirmModal", confirmModal);
});


function oswAskConfirmation({ title, message }) {
    return new Promise(resolve => window.dispatchEvent(new CustomEvent('open-confirm', {
        detail: { action: 'resolve', title, message, resolve }
    })));
}

// The sole modal coordinator tracks Alpine dialog visibility, focus and unsaved input.
function initOswDialogs() {
    const selector = '.osw-modal-shell[role="dialog"], .osw-modal-shell[role="alertdialog"]';
    const states = new Map();
    let stack = [];
    const visible = el => !!el && el.getClientRects().length > 0
        && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
    const top = () => stack[stack.length - 1] || null;
    const focusables = dialog => [...dialog.querySelectorAll(
        'button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [contenteditable="true"], [tabindex]:not([tabindex="-1"])'
    )].filter(el => visible(el) && !el.closest('[inert]'));
    const focus = dialog => {
        if (!visible(dialog) || top() !== dialog) return;
        const el = focusables(dialog).find(item => item.matches('input:not([type="hidden"]):not([type="checkbox"]), textarea, select')) || focusables(dialog)[0];
        if (el) el.focus({ preventScroll: true });
        else { dialog.tabIndex = -1; dialog.focus({ preventScroll: true }); }
    };
    const refresh = () => {
        const shown = [...document.querySelectorAll(selector)].filter(visible);
        for (const dialog of shown) {
            if (states.has(dialog)) continue;
            states.set(dialog, { trigger: document.activeElement, dirty: false });
            stack.push(dialog);
            requestAnimationFrame(() => focus(dialog));
        }
        for (const dialog of [...stack]) {
            if (shown.includes(dialog)) continue;
            const record = states.get(dialog);
            states.delete(dialog);
            stack = stack.filter(item => item !== dialog);
            requestAnimationFrame(() => {
                if (top()) focus(top());
                else if (record?.trigger?.isConnected && visible(record.trigger))
                    record.trigger.focus({ preventScroll: true });
            });
        }
        document.body.classList.toggle('osw-overlay-open', stack.length > 0);
    };
    const close = dialog => {
        const data = window.Alpine && Alpine.$data(dialog);
        if (!data) return;
        if (dialog.closest('[x-data="confirmModal"]')) data.close();
        else if (dialog.getAttribute('x-show') === 'imageImportOpen') data.closeImageImportDialog();
        else if (dialog.getAttribute('x-show') === 'memberEditor') data.memberEditor = false;
        else if (dialog.getAttribute('x-show') === 'advancedPreviewOpen') data.advancedPreviewOpen = false;
        else if (dialog.classList.contains('form-dialog')) data.editing = false;
        else data.show = false;
    };
    const requestClose = dialog => {
        const record = states.get(dialog);
        if (record?.dirty && !dialog.closest('[x-data="confirmModal"]') && !dialog.classList.contains('osw-group-preview-dialog')) {
            oswAskConfirmation({
                title: 'Discard unsaved changes?',
                message: 'This dialog contains unsaved changes. Discard them and close?'
            }).then(ok => { if (ok && visible(dialog)) close(dialog); });
        } else close(dialog);
    };
    document.addEventListener('osw-dialog-saved', event => {
        const dialog = event.target.closest && event.target.closest(selector);
        if (states.has(dialog)) states.get(dialog).dirty = false;
    }, true);
    for (const type of ['input', 'change']) {
        document.addEventListener(type, event => {
            if (!event.isTrusted) return;
            if (type === 'change' && event.target.matches &&
                event.target.matches('[x-model="selectedVersionId"]')) return;
            const dialog = event.target.closest && event.target.closest(selector);
            if (states.has(dialog)) states.get(dialog).dirty = true;
        }, true);
    }
    document.addEventListener('click', event => {
        const control = event.target.closest && event.target.closest('[data-osw-dialog-close]');
        if (!control) return;
        const dialog = control.closest(selector);
        if (!dialog) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        requestClose(dialog);
    }, true);
    document.addEventListener('keydown', event => {
        const dialog = top();
        if (!dialog) return;
        if (event.key === 'Escape') {
            // CKEditor's native popovers and OpenSigWeave's font listbox
            // consume Escape themselves; do not close the parent modal.
            if ([...document.querySelectorAll('.ck-dropdown__panel-visible, .osw-font-listbox:not([hidden])')].some(visible)) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            requestClose(dialog);
        } else if (event.key === 'Tab') {
            const items = focusables(dialog);
            if (!items.length) { event.preventDefault(); focus(dialog); return; }
            const first = items[0], last = items[items.length - 1];
            if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                event.preventDefault(); last.focus();
            } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                event.preventDefault(); first.focus();
            }
        }
    }, true);
    new MutationObserver(refresh).observe(document.body, {
        subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden']
    });
    refresh();
}

document.addEventListener("DOMContentLoaded", () => {
    initOswDialogs();
    const logoutButton = document.getElementById("logout-button");
    if (logoutButton) {
        logoutButton.addEventListener("click", () => {
            fetch("/logout", { method: "POST" })
                .then(response => response.json())
                .then(data => window.location.assign(data.logout_url))
                .catch(() => window.location.assign("/login"));
        });
    }
    const saveButton = document.getElementById("save-user-signature");
    if (saveButton) saveButton.addEventListener("click", saveUserSignature);

});
