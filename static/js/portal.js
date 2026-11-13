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

        var quill, domainQuill, adminQuill;
        const notify = (msg, type = 'success') => window.dispatchEvent(new CustomEvent('notify', { detail: { msg, type } }));

        // THE SMART LOCK ENGINE
        function toggleLock(lockId, rawId) {
            const rawElement = document.getElementById(rawId);
            const lockEl = document.getElementById(lockId);
            if (!rawElement || !lockEl) return;
            const rawVal = rawElement.value.toLowerCase();
            if (rawVal.includes('<table')) lockEl.classList.remove('hidden');
            else lockEl.classList.add('hidden');
        }
        function checkAllLocks() {
            toggleLock('user-quill-lock', 'raw-html-editor');
            toggleLock('domain-quill-lock', 'domain-raw-html-editor');
            toggleLock('admin-quill-lock', 'admin-raw-html-editor');
        }

        document.addEventListener('DOMContentLoaded', async function() {
            const userEditor = document.getElementById('quill-editor');
            const userRawEditor = document.getElementById('raw-html-editor');
            if (!userEditor || !userRawEditor || !window.Quill) return;

            const tb = [['bold', 'italic', 'underline'], [{ 'color': [] }, { 'background': [] }], [{ 'list': 'ordered'}, { 'list': 'bullet' }], ['link', 'image'], ['clean']];
            quill = new Quill(userEditor, { theme: 'snow', modules: { toolbar: tb }});

            quill.on('text-change', (d, old, source) => { if (source === 'user') { userRawEditor.value = quill.root.innerHTML; checkAllLocks(); } });
            userRawEditor.addEventListener('input', function() { quill.clipboard.dangerouslyPasteHTML(this.value); checkAllLocks(); });

            const domainEditor = document.getElementById('domain-quill-editor');
            const domainRawEditor = document.getElementById('domain-raw-html-editor');
            if (domainEditor && domainRawEditor) {
                domainQuill = new Quill(domainEditor, { theme: 'snow', modules: { toolbar: tb }});
                domainQuill.on('text-change', (d, old, source) => { if (source === 'user') { domainRawEditor.value = domainQuill.root.innerHTML; checkAllLocks(); } });
                domainRawEditor.addEventListener('input', function() { domainQuill.clipboard.dangerouslyPasteHTML(this.value); checkAllLocks(); });
            }

            if (document.body.dataset.canEdit !== "true") quill.disable();

            const res = await fetch('/api/my-signature');
            if(res.ok) {
                const data = await res.json();
                userRawEditor.value = data.html_content;
                quill.clipboard.dangerouslyPasteHTML(data.html_content);
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
                quill.clipboard.dangerouslyPasteHTML(cleanHtml);
            } else if (target === 'domain') {
                document.getElementById('domain-raw-html-editor').value = cleanHtml;
                domainQuill.clipboard.dangerouslyPasteHTML(cleanHtml);
            } else if (target === 'admin') {
                document.getElementById('admin-raw-html-editor').value = cleanHtml;
                if(adminQuill) adminQuill.clipboard.dangerouslyPasteHTML(cleanHtml);
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
    return {
        tab: "personal",
        adminTab: "domains",
        init() { if (document.body.dataset.idpSetupRequired === "true") { this.openAdmin(); this.adminTab = "identity-providers"; } },
        emit(name, detail) { window.dispatchEvent(new CustomEvent(name, { detail })); },
        openAdmin() { this.tab = "admin"; this.adminTab = "domains"; },
        confirmClearUser() { this.emit("open-confirm", { action: "clear-user" }); },
        confirmDomainToggle(domain) { this.emit("open-confirm", { domain, action: "toggle" }); },
        confirmDomainDelete(domain) { this.emit("open-confirm", { domain, action: "delete" }); },
        confirmClearAdmin(targetId) { this.emit("open-confirm", { action: "clear-admin", targetId }); },
        previewUserSignature() { this.emit("open-preview", { html: document.getElementById("raw-html-editor")?.value || "" }); },
        previewDomainSignature(domain) { this.emit("open-preview", { html: document.getElementById("domain-raw-html-editor")?.value || "", previewDomain: domain.domain_name }); },
        previewAdminSignature(email) { this.emit("open-preview", { html: document.getElementById("admin-raw-html-editor")?.value || "", previewEmail: email }); }
    };
}
function htmlEditorToggle() { return { showHtml: false }; }

function domainManager() { return {
    domains: [], providers: [],
    loginProviders() { return this.providers.filter(function(item) { if (!item.is_enabled || !item.configured) return false; if (item.provider_type === "ldap") return true; return item.provider_type === "authentik" && !!(item.settings && item.settings.OIDC_DISCOVERY_URL && item.settings.OIDC_CLIENT_ID && item.settings.OIDC_REDIRECT_URI); }); },
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
        if (response.ok) { this.providers = await response.json(); if (this.providers.length === 0) this.newProvider(); }
        else notify('Could not load identity providers.', 'error');
    },
    newProvider() { this.form={id:'',original_id:null,display_name:'',provider_type:'authentik',is_enabled:true,settings:{AUTHENTIK_TIMEOUT:'5'}}; this.editing=true; },
    changeType() { this.form.settings = this.form.provider_type === 'ldap' ? {LDAP_TIMEOUT:'5',LDAP_USER_FILTER:'(&(objectCategory=person)(objectClass=user)(mail={email}))'} : {AUTHENTIK_TIMEOUT:'5'}; },
    async editProvider(provider) {
        const response=await fetch('/api/admin/identity-providers');
        if(!response.ok) { notify('Could not load provider settings.','error'); return; }
        const item=(await response.json()).find(x=>x.id===provider.id);
        if(!item) return;
        const settings={...(item.settings||{})}; if(item.provider_type==='authentik') settings.AUTHENTIK_API_TOKEN=''; else settings.LDAP_BIND_PASSWORD='';
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
                signatures: [], domains: [], groups: [], selectedGroupSlug: '', variants: [], editing: false, variantEditor: false, variantSignature: null,
                form: { id: null, name: '', mode: 'AUTO', domain_ids: [], is_active: true, html_content: '', weight: 1 },
                get matchingSignatures() { return this.signatures.filter(item => item.is_active && item.mode === this.form.mode); },
                get availableGroups() { return this.groups.filter(group => group.is_active && group.slug); },
                insertGroupTag(slug, editor) {
                    if (!slug) { notify('Select a signature group first.', 'error'); return; }
                    const token = '{{ group:' + slug + ' }}';
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
                    this.form = { id: null, name: '', mode: 'AUTO', domain_ids: [], is_active: true, html_content: '', weight: 1 };
                    this.editing = true;
                },
                editSignature(signature) {
                    this.form = { id: signature.id, name: signature.name, mode: signature.mode,
                        domain_ids: Array.isArray(signature.domain_ids) ? signature.domain_ids.map(Number) : (signature.domain_id == null ? [] : [Number(signature.domain_id)]),
                        is_active: signature.is_active, html_content: '', weight: 1 };
                    this.editing = true;
                },
                async saveSignature() {
                    if (!this.form.name.trim()) { notify('Please enter a signature name.', 'error'); return; }
                    const domainIds = [...new Set(this.form.domain_ids.map(Number))];
                    if (!this.form.id && !this.form.html_content.trim()) { notify('HTML template is required.', 'error'); return; }
                    let response;
                    if (!this.form.id) {
                        response = await fetch('/api/admin/signatures', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.form.name, mode: this.form.mode, domain_ids: domainIds,
                                is_active: this.form.is_active, html_content: this.form.html_content, weight: this.form.weight }) });
                    } else {
                        response = await fetch('/api/admin/signatures/' + encodeURIComponent(this.form.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.form.name, domain_ids: domainIds, is_active: this.form.is_active }) });
                        if (response.ok && this.form.html_content.trim()) {
                            response = await fetch('/api/admin/signatures/' + encodeURIComponent(this.form.id) + '/versions',
                                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ html_content: this.form.html_content }) });
                        }
                    }
                    if (!response.ok) { notify('Could not save signature: ' + (await response.text()), 'error'); return; }
                    notify('Signature saved.');
                    this.editing = false;
                    await this.fetchAll();
                },
                async manageVariants(signature) {
                    this.variantSignature = signature;
                    const response = await fetch('/api/admin/signatures/' + encodeURIComponent(signature.id) + '/variants');
                    if (!response.ok) { notify('Could not load variants.', 'error'); return; }
                    this.variants = await response.json();
                    this.variantEditor = true;
                },
                async saveVariant(variant) {
                    const response = await fetch('/api/admin/signature-variants/' + encodeURIComponent(variant.id), {
                        method: 'PUT', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ weight: Number(variant.weight), is_enabled: variant.is_enabled })
                    });
                    if (!response.ok) { notify('Could not save variant: ' + (await response.text()), 'error'); return; }
                    notify('Variant updated.');
                    await this.fetchAll();
                }
            };
        }

        function signatureGroupManager() {
            return {
                groups: [], signatures: [], domains: [], editing: false, previewDomainId: '',
                form: { id: null, name: '', slug: '', domain_ids: [], is_active: true },
                memberForm: { group_id: '', signature_id: '', weight: 1 },
                async fetchAll() {
                    try {
                        const responses = await Promise.all([
                            fetch('/api/admin/signature-groups'), fetch('/api/admin/signatures'), fetch('/domains/')
                        ]);
                        if (responses.some(response => !response.ok)) throw new Error('Could not load signature group data');
                        [this.groups, this.signatures, this.domains] = await Promise.all(responses.map(response => response.json()));
                        if (!this.previewDomainId && this.domains.length) this.previewDomainId = String(this.domains[0].id);
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
                },
                async addMember() {
                    if (!this.memberForm.group_id || !this.memberForm.signature_id) { notify('Select a group and signature.', 'error'); return; }
                    const response = await fetch('/api/admin/signature-groups/' + encodeURIComponent(this.memberForm.group_id) + '/members', {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ signature_id: this.memberForm.signature_id, weight: Number(this.memberForm.weight), is_enabled: true })
                    });
                    if (!response.ok) { notify('Could not add signature: ' + (await response.text()), 'error'); return; }
                    this.memberForm.signature_id = ''; notify('Signature added to group.'); await this.fetchAll();
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
                }
            };
        }

        function ruleManager() {
            return {
                rules: [], signatures: [], groups: [], editing: false,
                form: { id: null, name: '', priority: 0, action_mode: 'AUTO', signature_id: '', group_id: '', is_enabled: true, conditions: [] },
                formatConditions(rule) {
                    return rule.conditions.length
                        ? rule.conditions.map(condition => condition.kind + ': ' + (condition.key ? condition.key + '=' : '') + condition.value).join(' AND ')
                        : 'Always matches';
                },
                get matchingSignatures() { return this.signatures.filter(item => item.is_active && item.mode === this.form.action_mode); },
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
                    this.form = { id: null, name: '', priority: 0, action_mode: 'AUTO', signature_id: '', group_id: '', is_enabled: true, conditions: [] };
                    this.editing = true;
                },
                editRule(rule) {
                    this.form = JSON.parse(JSON.stringify({ ...rule, signature_id: rule.signature_id || '', group_id: rule.group_id || '' }));
                    this.editing = true;
                },
                changeAction() { this.form.signature_id = ''; this.form.group_id = ''; },
                addCondition() { this.form.conditions.push({ kind: 'sender_equals', key: '', value: '' }); },
                async saveRule() {
                    if (!this.form.name.trim()) { notify('Please enter a rule name.', 'error'); return; }
                    if (this.form.conditions.some(condition => !condition.value.trim())) { notify('Every condition needs a value.', 'error'); return; }
                    const payload = { ...this.form,
                        signature_id: this.form.action_mode === 'GROUP' ? null : (this.form.signature_id || null),
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


        function domainSettingsModal() {
            return {
                show: false, domain: null, allowOverrides: true, injectOnReplies: false, trimWhitespace: true, stripDeviceSignatures: false, modalTab: 'settings', userOverrides: [], newOverrideEmail: '', editingUserEmail: '', editingUserDisabled: false,
                open(domainData) {
                    this.show = true; this.domain = domainData; this.allowOverrides = domainData.allow_overrides; this.injectOnReplies = domainData.inject_on_replies; this.trimWhitespace = domainData.trim_whitespace; this.stripDeviceSignatures = domainData.strip_device_signatures; this.modalTab = 'settings'; this.editingUserEmail = '';
                    
                    setTimeout(() => { 
                        document.getElementById('domain-raw-html-editor').value = domainData.template_html;
                        domainQuill.clipboard.dangerouslyPasteHTML(domainData.template_html); 
                        checkAllLocks();
                    }, 50);
                    
                    if(!adminQuill) {
                        adminQuill = new Quill('#admin-quill-editor', { theme: 'snow', modules: { toolbar: [['bold', 'italic', 'underline'], [{ 'color': [] }, { 'background': [] }], ['link', 'image'], ['clean']] }});
                        adminQuill.on('text-change', (d, old, source) => { if (source === 'user') { document.getElementById('admin-raw-html-editor').value = adminQuill.root.innerHTML; checkAllLocks(); } });
                        document.getElementById('admin-raw-html-editor').addEventListener('input', function() { adminQuill.clipboard.dangerouslyPasteHTML(this.value); checkAllLocks(); });
                    }
                },
                openUsers() {
                    this.modalTab = 'users';
                    this.fetchOverrides();
                },
                saveSettings() {
                    window.dispatchEvent(new CustomEvent('save-domain-settings', { detail: {
                        domain: this.domain,
                        allowOverrides: this.allowOverrides,
                        injectOnReplies: this.injectOnReplies,
                        trimWhitespace: this.trimWhitespace,
                        stripDeviceSignatures: this.stripDeviceSignatures,
                        html: document.getElementById('domain-raw-html-editor').value
                    }}));
                    this.show = false;
                },
                async fetchOverrides() {
                    const res = await fetch(`/api/admin/overrides/${this.domain.domain_name}`);
                    if(res.ok) this.userOverrides = await res.json();
                },
                loadAdminEditor(email, directHtml = null, isDisabled = false) {
                    if(!email || !email.includes('@')) { notify("Please enter a valid email address.", "error"); return; }
                    this.editingUserEmail = email; 
                    
                    if (directHtml !== null) { 
                        document.getElementById('admin-raw-html-editor').value = directHtml;
                        adminQuill.clipboard.dangerouslyPasteHTML(directHtml); 
                        this.editingUserDisabled = isDisabled;
                    } else {
                        const existingUser = this.userOverrides.find(u => u.user_email === email);
                        const content = existingUser ? existingUser.html_content : '';
                        document.getElementById('admin-raw-html-editor').value = content;
                        adminQuill.clipboard.dangerouslyPasteHTML(content);
                        this.editingUserDisabled = existingUser ? existingUser.is_disabled : false;
                    }
                    checkAllLocks();
                },
                async saveAdminOverride() {
                    const finalHtml = document.getElementById('admin-raw-html-editor').value;
                    const res = await fetch('/api/admin/overrides', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ user_email: this.editingUserEmail, html_content: finalHtml, is_disabled: this.editingUserDisabled }) });
                    if(res.ok) { notify(`Saved configuration for ${this.editingUserEmail}`); this.editingUserEmail = ''; this.fetchOverrides(); }
                }
            }
        }

        window.addEventListener('submit-new-domain', async (e) => {
            const res = await fetch('/domains/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain_name: e.detail, is_active: true }) });
            if (res.ok) { notify("Domain registered!"); window.dispatchEvent(new CustomEvent('domain-updated')); } else notify("Failed.", "error");
        });
        window.addEventListener('save-domain-settings', async (e) => {
            const res = await fetch(`/domains/${e.detail.domain.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: e.detail.domain.is_active, allow_overrides: e.detail.allowOverrides, inject_on_replies: e.detail.injectOnReplies, trim_whitespace: e.detail.trimWhitespace, strip_device_signatures: e.detail.stripDeviceSignatures, template_html: document.getElementById('domain-raw-html-editor').value }) });
            if (res.ok) { notify("Settings saved!"); window.dispatchEvent(new CustomEvent('domain-updated')); }
        });
        
        window.addEventListener('confirm-action', async (e) => {
            const { domain, action, targetId } = e.detail;
            if (action === 'toggle') {
                const res = await fetch(`/domains/${domain.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: !domain.is_active, allow_overrides: domain.allow_overrides, inject_on_replies: domain.inject_on_replies, trim_whitespace: domain.trim_whitespace, strip_device_signatures: domain.strip_device_signatures, template_html: domain.template_html }) });
                if (res.ok) { notify("Status updated."); window.dispatchEvent(new CustomEvent('domain-updated')); window.location.reload(); }
            } else if (action === 'delete') {
                const res = await fetch(`/domains/${domain.id}`, { method: 'DELETE' });
                if (res.ok) { notify("Domain deleted."); window.dispatchEvent(new CustomEvent('domain-updated')); window.location.reload(); }
            } else if (action === 'clear-user') {
                const res = await fetch('/api/my-signature', { method: 'DELETE' });
                if(res.ok) {
                    document.getElementById('raw-html-editor').value = '';
                    quill.setText(''); 
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

document.addEventListener("alpine:init", () => {
    Alpine.data("portalShell", portalShell);
    Alpine.data("htmlEditorToggle", htmlEditorToggle);
    Alpine.data("domainManager", domainManager);
    Alpine.data("identityProviderManager", identityProviderManager);
    Alpine.data("domainSettingsModal", domainSettingsModal);
    Alpine.data("signatureManager", signatureManager);
    Alpine.data("ruleManager", ruleManager);
    Alpine.data("toastManager", toastManager);
    Alpine.data("sanitizerModal", sanitizerModal);
    Alpine.data("previewModal", previewModal);
    Alpine.data("addDomainModal", addDomainModal);
    Alpine.data("confirmModal", confirmModal);
});

document.addEventListener("DOMContentLoaded", () => {
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

    const brand = document.querySelector(".app-brand");
    if (brand) {
        const color = brand.dataset.appColor || "";
        if (window.CSS && CSS.supports("color", color)) brand.style.color = color;
    }
});
