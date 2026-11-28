/* Controller-level behavior tests: no browser or external services required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/portal.js', 'utf8');

function getFactory(start, end, factory, context = {}) {
    const from = source.indexOf(start);
    const to = source.indexOf(end, from);
    assert.ok(from !== -1 && to > from, 'Expected controller boundary');
    return vm.runInNewContext(source.slice(from, to) + '\n' + factory + ';', context);
}

async function main() {
    const providerFactory = getFactory('function identityProviderManager() {',
        'function signatureManager()', 'identityProviderManager');
    const provider = providerFactory();
    provider.providers = [
        {id:'oidc', provider_type:'oidc', is_enabled:true, configured:true,
            oidc_secret_configured:true, settings:{OIDC_DISCOVERY_URL:'https://idp.example',
                OIDC_CLIENT_ID:'client', OIDC_REDIRECT_URI:'https://sig.example/auth/oidc'}},
        {id:'auth', provider_type:'authentik', is_enabled:true, configured:true,
            authentik_api_secret_configured:true, oidc_secret_configured:false,
            settings:{AUTHENTIK_URL:'https://auth.example', OIDC_CLIENT_ID:'client'}},
        {id:'ldap', provider_type:'ldap', is_enabled:true, configured:true,
            settings:{LDAP_URL:'ldaps://dc.example'}},
    ];
    assert.deepEqual(Array.from(provider.webProviders(), item=>item.id), ['oidc','auth','ldap']);
    assert.deepEqual(Array.from(provider.provisioningProviders(), item=>item.id), ['auth','ldap']);
    assert.equal(provider.webProviderReady(provider.providers[0]), true);
    assert.equal(provider.webProviderReady(provider.providers[1]), false);
    assert.equal(provider.provisioningProviderReady(provider.providers[1]), true);
    assert.equal(provider.provisioningProviders().some(item=>item.id==='oidc'), false);
    provider.newWebProvider();
    assert.equal(provider.editorSection,'web');
    assert.equal(provider.form.provider_type,'oidc');
    provider.newProvisioningProvider();
    assert.equal(provider.editorSection,'provisioning');
    assert.equal(provider.form.provider_type,'authentik');
    provider.editProvider(provider.providers[1], 'web');
    assert.equal(provider.form.settings.AUTHENTIK_URL,'https://auth.example');
    assert.equal(provider.form.settings.AUTHENTIK_API_TOKEN,'');
    assert.equal(provider.form.settings.OIDC_CLIENT_SECRET,'');

    let confirmed = true;
    const calls = [];
    const messages = [];
    const ctx = {
        oswAskConfirmation: async ({title,message}) => { calls.push({title,message}); return confirmed; },
        fetch: async (url, opts) => {
            calls.push({url,opts});
            return {ok:true,json:async()=>({deleted_ids:[1,2],not_found_ids:[3],out_of_scope_ids:[]})};
        },
        notify: (message,type) => messages.push({message,type}),
    };
    const domainFactory = getFactory('function domainSettingsModal() {',
        "window.addEventListener('submit-new-domain'", 'domainSettingsModal', ctx);
    const controller = domainFactory();
    controller.domain={domain_name:'example.org',id:10};
    controller.userOverrides=Array.from({length:55},(_,index)=>({
        id:index+1, user_email:'user'+(index+1)+'@example.org', html_content:'test'
    }));
    controller.toggleAllOverrides(true);
    assert.equal(controller.selectedOverrideCount(),55);
    controller.overridePage=3;
    assert.equal(controller.displayedOverrides().length,15);
    controller.overrideQuery='user1';
    assert.equal(controller.filteredOverrides().length,11);
    assert.equal(controller.selectedOverrideCount(),55,'Filtering cannot silently change selection');
    controller.toggleOverrideSelected(15,false);
    assert.equal(controller.selectedOverrideCount(),54);
    controller.toggleAllOverrides(false);
    assert.equal(controller.selectedOverrideCount(),0);
    controller.toggleOverrideSelected(1,true);
    controller.toggleOverrideSelected(2,true);
    controller.toggleOverrideSelected(3,true);
    controller.fetchOverrides=async()=>{};
    controller.fetchUsers=async()=>{};
    confirmed=false;
    await controller.deleteSelectedOverrides();
    assert.equal(calls.filter(item=>item.url).length,0,'Cancel must not make a request');
    confirmed=true;
    await controller.deleteSelectedOverrides();
    const outbound=calls.find(item=>item.url);
    assert.equal(outbound.url,'/api/admin/overrides/bulk-delete');
    assert.equal(outbound.opts.method,'POST');
    const parsed=JSON.parse(outbound.opts.body);
    assert.equal(parsed.domain_name,'example.org');
    assert.deepEqual(parsed.ids,[1,2,3]);
    assert.equal(controller.selectedOverrideCount(),0);
    assert.ok(messages.some(item=>item.message.includes('2 user override(s) removed')));
    assert.ok(messages.some(item=>item.message.includes('1 missing')));
    assert.ok(calls[0].message.includes('exactly 3 selected override(s)'));
    console.log('Identity & User Management controllers: OK');
}

main().catch(error=>{console.error(error);process.exitCode=1;});
