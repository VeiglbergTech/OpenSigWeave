const userData = JSON.parse(document.body.dataset.userData || "{}");
const opensigweaveCsrfToken = document.body.dataset.csrfToken || "";
const originalFetch = window.fetch.bind(window);
window.fetch = (resource, options = {}) => {
    const method = (options.method || "GET").toUpperCase();
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
        const headers = new Headers(options.headers || {});
        headers.set("X-CSRF-Token", opensigweaveCsrfToken);
        options = { ...options, headers };
    }
    return originalFetch(resource, options);
};

        var userRichEditor, domainRichEditor, adminRichEditor, signatureRichEditor;
        const notify = (msg, type = 'success') => window.dispatchEvent(new CustomEvent('notify', { detail: { msg, type } }));

        async function mountRichEditor(hostId, rawId, options = {}) {
            const host = document.getElementById(hostId);
            const raw = document.getElementById(rawId);
            if (!host || !raw || !window.OpenSigWeaveEditor) return null;
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
            const adapter = await window.OpenSigWeaveEditor.create(target, {
                initialData: raw.value,
                readOnly: !!options.readOnly,
                imageUpload: !!options.imageUpload,
                uploadUrl: options.uploadUrl,
                csrfToken: options.csrfToken,
                onChange: html => {
                    raw.value = html;
                    if (typeof options.onChange === "function") options.onChange(html);
                    checkAllLocks();
                }
            });
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
            const res = await fetch('/api/my-signature');
            if (res.ok) {
                const data = await res.json();
                userRawEditor.value = data.html_content || "";
                if (userRichEditor) userRichEditor.setData(userRawEditor.value);
                checkAllLocks();
            }
        });

        window.addEventListener('sanitize-html', (e) => {
            const { html, target } = e.detail;
            if (!html) return;

            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');
            let cleanHtml = doc.body.innerHTML.trim();
            cleanHtml = cleanHtml.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '');

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
            if(res.ok) notify("Signature saved successfully!"); else notify("Failed to save.", "error");
        }

        function portalShell() {
    const stateKey = "opensigweave.portal-state.v1";
    const isAdmin = document.body.dataset.isAdmin === "true";
    const allowedAdminTabs = new Set([
        "identity-providers", "domains", "signatures", "signature-groups",
        "rules", "scheduling", "branding", "exports", "imports", "system"
    ]);
    let saved = {};
    try { saved = JSON.parse(sessionStorage.getItem(stateKey) || "{}"); } catch (_error) {}
    const initialUrl = new URL(window.location.href);
    const directSection = initialUrl.searchParams.get("section");
    const directView = initialUrl.searchParams.get("view");
    const initialAdminTab = allowedAdminTabs.has(directSection) ? directSection
        : (allowedAdminTabs.has(saved.adminTab) ? saved.adminTab : "identity-providers");
    const initialView = isAdmin && (
        directView === "admin" || (directView !== "personal" && !directView && (
            allowedAdminTabs.has(directSection) || saved.tab === "admin" ||
            document.body.dataset.idpSetupRequired === "true"
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
            window.addEventListener("popstate", () => {
                const url = new URL(window.location.href);
                const view = url.searchParams.get("view");
                const section = url.searchParams.get("section");
                this.adminTab = allowedAdminTabs.has(section) ? section : this.adminTab;
                this.tab = isAdmin && (view === "admin" || (!view && allowedAdminTabs.has(section)))
                    ? "admin" : "personal";
                try { sessionStorage.setItem(stateKey, JSON.stringify({tab: this.tab, adminTab: this.adminTab})); }
                catch (_error) {}
            });
        },
        setAdminTab(section) {
            if (!isAdmin || !allowedAdminTabs.has(section)) return;
            this.adminTab = section;
            this.tab = "admin";
            this.saveNavigation();
        },
        openPersonal() { this.tab = "personal"; this.saveNavigation(); },
        openAdmin() { if (!isAdmin) return; this.tab = "admin"; this.saveNavigation(); },
        emit(name, detail) { window.dispatchEvent(new CustomEvent(name, { detail })); },
        confirmClearUser() { this.emit("open-confirm", { action: "clear-user" }); },
        confirmDomainToggle(domain) { this.emit("open-confirm", { domain, action: "toggle" }); },
        confirmDomainDelete(domain) { this.emit("open-confirm", { domain, action: "delete" }); },
        confirmClearAdmin(targetId) { this.emit("open-confirm", { action: "clear-admin", targetId }); },
        previewUserSignature() { this.emit("open-preview", { html: document.getElementById("raw-html-editor")?.value || "" }); },
        previewAdminSignature(email) { this.emit("open-preview", { html: document.getElementById("admin-raw-html-editor")?.value || "", previewEmail: email }); }
    };
}
function htmlEditorToggle() { return { showHtml: false }; }

function domainManager() { return {
    domains: [], providers: [],
    loginProviders() { return this.providers.filter(function(item) { if (!item.is_enabled || !item.configured) return false; if (item.provider_type === "ldap") return true; return (item.provider_type === "authentik" || item.provider_type === "oidc") && !!(item.settings && item.settings.OIDC_DISCOVERY_URL && item.settings.OIDC_CLIENT_ID && item.settings.OIDC_REDIRECT_URI); }); },
    async fetchDomains() {
        const [domainsResponse, providersResponse] = await Promise.all([fetch('/domains/'), fetch('/api/admin/identity-providers')]);
        this.domains = domainsResponse.ok ? await domainsResponse.json() : [];
        this.providers = providersResponse.ok ? await providersResponse.json() : [];
    },
    async setProvider(domain, providerId) {
        const response = await fetch('/domains/' + domain.id + '/identity-provider', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({identity_provider_id: providerId || null})});
        if (!response.ok) { notify('Could not assign identity provider.', 'error'); await this.fetchDomains(); return; }
        domain.identity_provider_id = providerId || null;
        notify('Identity provider assignment saved.');
    },
    async setAuthenticationProvider(domain, providerId) {
        const response = await fetch('/domains/' + domain.id + '/authentication-provider', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({authentication_provider_id: providerId || null})});
        if (!response.ok) { notify('Could not assign authentication provider.', 'error'); await this.fetchDomains(); return; }
        domain.authentication_provider_id = providerId || null;
        notify('Authentication provider assignment saved.');
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
        if(!this.form.id.trim() || !this.form.display_name.trim()) { notify('Provider ID and display name are required.','error'); return; }
        const payload={id:this.form.id,display_name:this.form.display_name,provider_type:this.form.provider_type,is_enabled:this.form.is_enabled,settings:this.form.settings};
        const url=this.form.original_id ? '/api/admin/identity-providers/'+encodeURIComponent(this.form.original_id) : '/api/admin/identity-providers';
        const response=await fetch(url,{method:this.form.original_id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(!response.ok) { notify('Could not save provider: '+(await response.text()),'error'); return; }
        notify('Identity provider saved.');
        this.editing=false; await this.fetchAll(); window.dispatchEvent(new Event('domain-updated'));
    },
    async testProvider(provider) {
        const response=await fetch('/api/admin/identity-providers/'+encodeURIComponent(provider.id)+'/test',{method:'POST'});
        if(response.ok) notify('Identity provider connection successful.');
        else notify('Connection test failed. Check the configuration and application logs.','error');
    },
    async deleteProvider(provider) {
        if(!confirm('Delete identity provider '+provider.display_name+'? Unassign it from all domains first.')) return;
        const response=await fetch('/api/admin/identity-providers/'+encodeURIComponent(provider.id),{method:'DELETE'});
        if(!response.ok) { notify('Could not delete provider: '+(await response.text()),'error'); return; }
        notify('Identity provider deleted.'); await this.fetchAll(); window.dispatchEvent(new Event('domain-updated'));
    }
} }
        function signatureManager() {
            return {
                signatures: [], domains: [], groups: [], selectedGroupSlug: '', versions: [], selectedVersionId: '', loadedVersionHtml: '', loadedEditorHtml: '', versionRequestToken: 0, editing: false,
                form: { id: null, name: '', domain_ids: [], is_active: true, html_content: '' },
                get availableGroups() { return this.groups.filter(group => group.is_active && group.slug); },
                get currentVersion() { return this.versions.find(version => version.is_current) || this.versions[0] || null; },
                get selectedVersion() { return this.versions.find(version => version.id === this.selectedVersionId) || null; },
                get isViewingHistoricalVersion() {
                    return Boolean(this.currentVersion && this.selectedVersion && this.currentVersion.id !== this.selectedVersion.id);
                },
                insertGroupTag(slug, editor) {
                    if (!slug) { notify('Select a signature group first.', 'error'); return; }
                    const token = '{{ group:' + slug + ' }}';
                    if (signatureRichEditor) {
                        signatureRichEditor.insertText(token);
                        this.form.html_content = signatureRichEditor.getData();
                        return;
                    }
                    const start = editor ? editor.selectionStart : this.form.html_content.length;
                    const end = editor ? editor.selectionEnd : start;
                    this.form.html_content = this.form.html_content.slice(0, start) + token + this.form.html_content.slice(end);
                    if (editor) this.$nextTick(() => { editor.focus(); editor.setSelectionRange(start + token.length, start + token.length); });
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
                        const [signaturesResponse, domainsResponse, groupsResponse] = await Promise.all([
                            fetch('/api/admin/signatures'), fetch('/domains/'), fetch('/api/admin/signature-groups')
                        ]);
                        if (!signaturesResponse.ok || !domainsResponse.ok || !groupsResponse.ok) throw new Error('Could not load signature catalog');
                        [this.signatures, this.domains, this.groups] = await Promise.all([
                            signaturesResponse.json(), domainsResponse.json(), groupsResponse.json()
                        ]);
                        if (!this.availableGroups.some(group => group.slug === this.selectedGroupSlug)) {
                            this.selectedGroupSlug = this.availableGroups[0]?.slug || '';
                        }
                    } catch (error) { notify(error.message, 'error'); }
                },
                newSignature() {
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
                groups: [], signatures: [], domains: [], editing: false, memberEditor: false, previewDomainId: '',
                form: { id: null, name: '', slug: '', domain_ids: [], is_active: true },
                memberForm: { group_id: '', signature_id: '', weight: 1 },
                async fetchAll() {
                    try {
                        const responses = await Promise.all([
                            fetch('/api/admin/signature-groups'), fetch('/api/admin/signatures'), fetch('/domains/')
                        ]);
                        if (responses.some(response => !response.ok)) throw new Error('Could not load signature group data');
                        [this.groups, this.signatures, this.domains] = await Promise.all(responses.map(response => response.json()));
                        if (!this.previewDomainId && this.domains.length) this.previewDomainId = this.domains[0].id.toString();
                    } catch (error) { notify(error.message, 'error'); }
                },
                signatureName(id) {
                    const item = this.signatures.find(signature => signature.id === id);
                    return item ? item.name : 'Missing signature';
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
                    if (!window.confirm('Remove this signature from the group?')) return;
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(group.id) + '/members/' + encodeURIComponent(member.signature_id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not remove group member.', 'error'); return; }
                    await this.fetchAll();
                },
                async previewGroup(group) {
                    if (!this.previewDomainId) { notify('Create or select a domain before previewing.', 'error'); return; }
                    const url = '/api/admin/signature-groups/' + encodeURIComponent(group.id)
                        + '/preview?domain_id=' + encodeURIComponent(this.previewDomainId);
                    const response = await fetch(url, { headers: { 'Accept': 'application/json' } });
                    if (!response.ok) { notify('Could not render group preview: ' + (await response.text()), 'error'); return; }
                    const result = await response.json();
                    window.dispatchEvent(new CustomEvent('open-preview', { detail: { html: result.html } }));
                    notify('Preview uses a deterministic member and does not change rotation history.');
                },
                async deleteGroup(group) {
                    if (!window.confirm('Delete signature group "' + group.name + '"?')) return;
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(group.id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete group: ' + (await response.text()), 'error'); return; }
                    notify('Group deleted.'); await this.fetchAll();
                    window.dispatchEvent(new window.Event('signature-groups-updated'));
                }
            };
        }

        function ruleManager() {
            return {
                rules: [], signatures: [], groups: [], editing: false,
                form: { id: null, name: '', priority: 0, action_mode: 'SIGNATURE', signature_id: '', group_id: '', is_enabled: true, conditions: [] },
                formatConditions(rule) {
                    return rule.conditions.length
                        ? rule.conditions.map(condition => condition.kind + ': ' + (condition.key ? condition.key + '=' : '') + condition.value).join(' AND ')
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
                    try {
                        const responses = await Promise.all([
                            fetch('/api/admin/rules'), fetch('/api/admin/signatures'), fetch('/api/admin/signature-groups')
                        ]);
                        if (responses.some(response => !response.ok)) throw new Error('Could not load rule data');
                        [this.rules, this.signatures, this.groups] = await Promise.all(responses.map(response => response.json()));
                    } catch (error) { notify(error.message, 'error'); }
                },
                newRule() {
                    this.form = { id: null, name: '', priority: 0, action_mode: 'SIGNATURE', signature_id: '', group_id: '', is_enabled: true, conditions: [] };
                    this.editing = true;
                },
                editRule(rule) {
                    const normalizedAction = rule.action_mode === 'GROUP'
                        ? 'GROUP'
                        : (rule.action_mode === 'NONE' ? 'NONE' : 'SIGNATURE');
                    this.form = JSON.parse(JSON.stringify({
                        ...rule,
                        action_mode: normalizedAction,
                        signature_id: rule.signature_id || '',
                        group_id: rule.group_id || ''
                    }));
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
                async saveRule() {
                    if (!this.form.name.trim()) { notify('Please enter a rule name.', 'error'); return; }
                    if (this.form.conditions.some(condition => !condition.value.trim())) { notify('Every condition needs a value.', 'error'); return; }
                    if (this.form.action_mode === 'SIGNATURE' && !this.form.signature_id) { notify('Select a signature.', 'error'); return; }
                    if (this.form.action_mode === 'GROUP' && !this.form.group_id) { notify('Select a signature group.', 'error'); return; }
                    const payload = { ...this.form,
                        signature_id: this.form.action_mode === 'SIGNATURE' ? (this.form.signature_id || null) : null,
                        group_id: this.form.action_mode === 'GROUP' ? (this.form.group_id || null) : null };
                    const url = this.form.id ? '/api/admin/rules/' + encodeURIComponent(this.form.id) : '/api/admin/rules';
                    const response = await fetch(url, { method: this.form.id ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    if (!response.ok) { notify('Could not save rule: ' + (await response.text()), 'error'); return; }
                    notify('Signature rule saved.'); this.editing = false; await this.fetchAll();
                },
                async deleteRule(rule) {
                    if (!window.confirm('Delete rule "' + rule.name + '"?')) return;
                    const response = await fetch('/api/admin/rules/' + encodeURIComponent(rule.id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete rule.', 'error'); return; }
                    notify('Signature rule deleted.'); await this.fetchAll();
                }
            };
        }


        function scheduleManager() {
            return {
                schedules: [], signatures: [], groups: [], domains: [], editing: false,
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
                changeTargetType() {
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
                        notify('Enter a valid start and end time.', 'error'); return;
                    }
                    const payload = {
                        name: this.form.name.trim(), target_type: this.form.target_type,
                        signature_id: this.form.target_type === 'SIGNATURE' ? this.form.signature_id : null,
                        group_id: this.form.target_type === 'GROUP' ? this.form.group_id : null,
                        domain_id: this.form.domain_id === '' ? null : Number(this.form.domain_id),
                        starts_at: start.toISOString(), ends_at: end.toISOString(),
                        priority: Number(this.form.priority), is_enabled: this.form.is_enabled
                    };
                    const url = this.form.id ? '/api/admin/schedules/' + encodeURIComponent(this.form.id) : '/api/admin/schedules';
                    const response = await fetch(url, { method: this.form.id ? 'PUT' : 'POST',
                        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    if (!response.ok) {
                        const problem = await response.json().catch(() => ({}));
                        notify('Could not save schedule: ' + (problem.detail || response.statusText), 'error'); return;
                    }
                    notify('Schedule saved.');
                    this.editing = false;
                    await this.fetchAll();
                },
                async deleteSchedule(schedule) {
                    if (!window.confirm('Delete schedule "' + schedule.name + '"?')) return;
                    const response = await fetch('/api/admin/schedules/' + encodeURIComponent(schedule.id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete schedule.', 'error'); return; }
                    notify('Schedule deleted.');
                    await this.fetchAll();
                }
            };
        }

        function brandingManager() {
            return {
                settings: { app_name: 'OpenSigWeave', app_color: '#ff7043', logo_url: '/branding/logo.png', favicon_url: '/branding/favicon' },
                logoDataUrl: null, faviconDataUrl: null, resetLogo: false, resetFavicon: false, cacheKey: Date.now(), saving: false,
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
                async chooseLogo(event) {
                    const file = event.target.files && event.target.files[0];
                    if (!file) return;
                    this.logoDataUrl = null;
                    if (!file.size || file.size > 512 * 1024 || file.type !== 'image/png') {
                        notify('Logo must be a PNG up to 512 KiB.', 'error');
                        event.target.value = '';
                        return;
                    }
                    try { this.logoDataUrl = await this.readFile(file, 'image/png'); this.resetLogo = false; }
                    catch (error) { notify(error.message, 'error'); event.target.value = ''; }
                },
                async chooseFavicon(event) {
                    const file = event.target.files && event.target.files[0];
                    if (!file) return;
                    this.faviconDataUrl = null;
                    const ico = file.name.toLowerCase().endsWith('.ico');
                    if (!file.size || file.size > 256 * 1024 || (!ico && file.type !== 'image/png')) {
                        notify('Favicon must be PNG or ICO up to 256 KiB.', 'error');
                        event.target.value = '';
                        return;
                    }
                    try { this.faviconDataUrl = await this.readFile(file, ico ? 'image/x-icon' : 'image/png'); this.resetFavicon = false; }
                    catch (error) { notify(error.message, 'error'); event.target.value = ''; }
                },
                restoreLogo() { this.logoDataUrl = null; this.resetLogo = true; },
                restoreFavicon() { this.faviconDataUrl = null; this.resetFavicon = true; },
                async save() {
                    if (this.saving) return;
                    if (!this.settings.app_name || !this.settings.app_name.trim()) { notify('Application name is required.', 'error'); return; }
                    if (!/^#[0-9a-fA-F]{6}$/.test(this.settings.app_color || '')) { notify('Accent color must be a six-digit hex value.', 'error'); return; }
                    this.saving = true;
                    try {
                        const response = await fetch('/api/admin/branding', {
                            method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                app_name: this.settings.app_name.trim(), app_color: this.settings.app_color,
                                logo_data_url: this.logoDataUrl, favicon_data_url: this.faviconDataUrl,
                                reset_logo: this.resetLogo, reset_favicon: this.resetFavicon
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
                users: [], userQuery: '', userSource: 'all', userPage: 1, userTotal: 0,
                userLoading: false, userError: '', userWarning: '', userRequestId: 0,
                async open(domainData) {
                    this.show = true; this.domain = domainData; this.allowOverrides = domainData.allow_overrides; this.injectOnReplies = domainData.inject_on_replies; this.trimWhitespace = domainData.trim_whitespace; this.stripDeviceSignatures = domainData.strip_device_signatures; this.modalTab = 'settings'; this.editingUserEmail = '';
                    await this.$nextTick();
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

                saveSettings() {
                    window.dispatchEvent(new CustomEvent('save-domain-settings', { detail: {
                        domain: this.domain,
                        allowOverrides: this.allowOverrides,
                        injectOnReplies: this.injectOnReplies,
                        trimWhitespace: this.trimWhitespace,
                        stripDeviceSignatures: this.stripDeviceSignatures
                    }}));
                    this.show = false;
                },
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
                        const content = existingUser ? existingUser.html_content : '';
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
                    if(res.ok) { notify(`Saved configuration for ${this.editingUserEmail}`); this.editingUserEmail = ''; this.fetchOverrides(); this.fetchUsers(); }
                }
            }
        }

        window.addEventListener('submit-new-domain', async (e) => {
            const res = await fetch('/domains/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain_name: e.detail, is_active: true }) });
            if (res.ok) { notify("Domain registered!"); window.dispatchEvent(new CustomEvent('domain-updated')); } else notify("Failed.", "error");
        });
        window.addEventListener('save-domain-settings', async (e) => {
            const res = await fetch(`/domains/${e.detail.domain.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: e.detail.domain.is_active, allow_overrides: e.detail.allowOverrides, inject_on_replies: e.detail.injectOnReplies, trim_whitespace: e.detail.trimWhitespace, strip_device_signatures: e.detail.stripDeviceSignatures }) });
            if (res.ok) { notify("Settings saved!"); window.dispatchEvent(new CustomEvent('domain-updated')); }
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
                    document.getElementById('raw-html-editor').value = '';
                    if (userRichEditor) userRichEditor.setData(''); 
                    checkAllLocks();
                    notify("Signature cleared and removed from database!");
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
        closeOnEscape(event) {
            if (!this.show) return;
            event.stopImmediatePropagation();
            this.show = false;
        },
        open(event) {
            this.show = true;
            const detail = event.detail || {};
            const email = detail.previewEmail || userData.email;
            const domain = detail.previewDomain || email.split('@')[1] || '';
            let first = userData.first_name;
            let last = userData.last_name;
            let title = userData.title;
            let phone = userData.phone;
            if (detail.previewEmail) {
                const nameParts = email.split('@')[0].split('.');
                first = nameParts[0] ? nameParts[0].charAt(0).toUpperCase() + nameParts[0].slice(1) : 'User';
                last = nameParts[1] ? nameParts[1].charAt(0).toUpperCase() + nameParts[1].slice(1) : '';
                title = 'Title (Preview)';
                phone = 'Phone (Preview)';
            }
            const html = String(detail.html || '')
                .replace(/\{\{\s*first_name\s*\}\}/g, first)
                .replace(/\{\{\s*last_name\s*\}\}/g, last)
                .replace(/\{\{\s*title\s*\}\}/g, title)
                .replace(/\{\{\s*phone\s*\}\}/g, phone)
                .replace(/\{\{\s*email\s*\}\}/g, email)
                .replace(/\{\{\s*domain_name\s*\}\}/g, domain);
            const frame = document.getElementById('signature-preview-frame');
            if (frame) frame.srcdoc = html;
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
        show: false,
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
            if (!window.confirm("Import the reviewed configuration now? Existing records will never be overwritten.")) return;
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


function systemStatusManager() {
    const labels = {
        domains: "Domains", identity_providers: "Identity providers",
        signatures: "Signatures", versions: "Versions", groups: "Groups",
        rules: "Rules", schedules: "Schedules", overrides: "User overrides"
    };
    return {
        loading: false, error: "", state: "Not checked", version: "—",
        checkedAt: "—", database: "Unknown", storage: "Unknown",
        assets: "Unknown", localAdmin: "Unknown", managementApi: "Unknown",
        records: [],
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
                if (!response.ok) throw new Error(data.detail || "Status request failed");
                this.state = data.status === "healthy" ? "Healthy" : "Degraded";
                this.version = data.version || "Unknown";
                this.checkedAt = data.checked_at ? new Date(data.checked_at).toLocaleString() : "Unknown";
                this.database = data.database?.status || "Unknown";
                this.storage = data.storage?.status || "Unknown";
                this.assets = data.storage?.image_assets ? "Accessible" : "Unavailable";
                this.localAdmin = data.configuration?.local_admin_enabled ? "Configured" : "Unavailable";
                this.managementApi = data.configuration?.management_api_enabled ? "Enabled" : "Disabled";
                this.records = Object.entries(data.counts || {}).map(([key, count]) => ({
                    key, label: labels[key] || key, count
                }));
            } catch (error) {
                this.error = "Could not load system status: " + (error instanceof Error ? error.message : "Unknown error");
                this.state = "Unavailable";
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
    Alpine.data("systemStatusManager", systemStatusManager);
    Alpine.data("toastManager", toastManager);
    Alpine.data("sanitizerModal", sanitizerModal);
    Alpine.data("previewModal", previewModal);
    Alpine.data("addDomainModal", addDomainModal);
    Alpine.data("confirmModal", confirmModal);
});

document.addEventListener("DOMContentLoaded", () => {
    // Lock background scrolling whenever a visible overlay is open.
    const overlaySelectors = ".form-dialog, [x-data='domainSettingsModal'], [x-data='previewModal'], [x-data='sanitizerModal'], [x-data='addDomainModal'], [x-data='confirmModal']";
    const active = node => node.getClientRects().length > 0 && getComputedStyle(node).visibility !== "hidden";
    const updateOverlays = () => {
        const nodes = [...document.querySelectorAll(overlaySelectors)];
        document.body.classList.toggle("osw-overlay-open", nodes.some(active));
        document.body.classList.toggle("osw-floating-dialog-open", nodes.some(node => node.matches(".form-dialog") && active(node)));
    };
    new MutationObserver(updateOverlays).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["style", "class"] });
    updateOverlays();
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
