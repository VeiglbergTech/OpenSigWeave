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

        function domainManager() { return { domains: [], async fetchDomains() { this.domains = await (await fetch('/domains/')).json(); } } }
        function signatureManager() {
            return {
                signatures: [], domains: [], variants: [], editing: false, variantEditor: false, variantSignature: null,
                form: { id: null, name: '', mode: 'AUTO', domain_id: '', is_active: true, html_content: '', weight: 1 },
                get matchingSignatures() { return this.signatures.filter(item => item.is_active && item.mode === this.form.mode); },
                async fetchAll() {
                    try {
                        const [signaturesResponse, domainsResponse] = await Promise.all([fetch('/api/admin/signatures'), fetch('/domains/')]);
                        if (!signaturesResponse.ok || !domainsResponse.ok) throw new Error('Could not load signature catalog');
                        this.signatures = await signaturesResponse.json();
                        this.domains = await domainsResponse.json();
                    } catch (error) { notify(error.message, 'error'); }
                },
                newSignature() {
                    this.form = { id: null, name: '', mode: 'AUTO', domain_id: '', is_active: true, html_content: '', weight: 1 };
                    this.editing = true;
                },
                editSignature(signature) {
                    this.form = { id: signature.id, name: signature.name, mode: signature.mode,
                        domain_id: signature.domain_id == null ? '' : String(signature.domain_id),
                        is_active: signature.is_active, html_content: '', weight: 1 };
                    this.editing = true;
                },
                async saveSignature() {
                    if (!this.form.name.trim()) { notify('Please enter a signature name.', 'error'); return; }
                    const domainId = this.form.domain_id === '' ? null : Number(this.form.domain_id);
                    if (!this.form.id && !this.form.html_content.trim()) { notify('HTML template is required.', 'error'); return; }
                    let response;
                    if (!this.form.id) {
                        response = await fetch('/api/admin/signatures', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.form.name, mode: this.form.mode, domain_id: domainId,
                                is_active: this.form.is_active, html_content: this.form.html_content, weight: this.form.weight }) });
                    } else {
                        response = await fetch('/api/admin/signatures/' + encodeURIComponent(this.form.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: this.form.name, domain_id: domainId, is_active: this.form.is_active }) });
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

        function ruleManager() {
            return {
                rules: [], signatures: [], editing: false,
                form: { id: null, name: '', priority: 0, action_mode: 'AUTO', signature_id: '', is_enabled: true, conditions: [] },
                get matchingSignatures() { return this.signatures.filter(item => item.is_active && item.mode === this.form.action_mode); },
                async fetchAll() {
                    try {
                        const [rulesResponse, signaturesResponse] = await Promise.all([
                            fetch('/api/admin/rules'), fetch('/api/admin/signatures')
                        ]);
                        if (!rulesResponse.ok || !signaturesResponse.ok) throw new Error('Could not load rule data');
                        this.rules = await rulesResponse.json();
                        this.signatures = await signaturesResponse.json();
                    } catch (error) { notify(error.message, 'error'); }
                },
                newRule() {
                    this.form = { id: null, name: '', priority: 0, action_mode: 'AUTO', signature_id: '', is_enabled: true, conditions: [] };
                    this.editing = true;
                },
                editRule(rule) {
                    this.form = JSON.parse(JSON.stringify({ ...rule, signature_id: rule.signature_id || '' }));
                    this.editing = true;
                },
                addCondition() { this.form.conditions.push({ kind: 'sender_equals', key: '', value: '' }); },
                async saveRule() {
                    if (!this.form.name.trim()) { notify('Please enter a rule name.', 'error'); return; }
                    if (this.form.conditions.some(c => !c.value.trim())) { notify('Every condition needs a value.', 'error'); return; }
                    const payload = { ...this.form, signature_id: this.form.signature_id || null };
                    const url = this.form.id ? '/api/admin/rules/' + encodeURIComponent(this.form.id) : '/api/admin/rules';
                    const response = await fetch(url, { method: this.form.id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    if (!response.ok) { notify('Could not save rule: ' + (await response.text()), 'error'); return; }
                    notify('Signature rule saved.');
                    this.editing = false;
                    await this.fetchAll();
                },
                async deleteRule(rule) {
                    if (!window.confirm('Delete rule "' + rule.name + '"?')) return;
                    const response = await fetch('/api/admin/rules/' + encodeURIComponent(rule.id), { method: 'DELETE' });
                    if (!response.ok) { notify('Could not delete rule.', 'error'); return; }
                    notify('Signature rule deleted.');
                    await this.fetchAll();
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


function formatRuleConditions(rule) {
    return rule.conditions.length
        ? rule.conditions.map(condition => condition.kind + ': ' + (condition.key ? condition.key + '=' : '') + condition.value).join(' AND ')
        : 'Always matches';
}

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
