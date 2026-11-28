-- Offline policy decisions with a controlled parser, I/O and clock.
package.path = 'rspamd/?.lua;' .. package.path
local fixture
local file_available = true
local saved_open, saved_time = io.open, os.time
io.open = function(path, mode)
    if not file_available or path ~= '/tmp/osw-policy-fixture.json' then return nil end
    return {read=function(_, bytes) return 'OSW_POLICY_FIXTURE' end, close=function() end}
end
local clock = 1000
os.time = function() return clock end
package.preload['ucl'] = function() return {parser=function()
    return {parse_string=function() return true end, get_object=function() return fixture end}
end} end
local policy = require 'opensigweave_domain_policy'
local opts = {domain_policy_file='/tmp/osw-policy-fixture.json'}
local t = {
    get_user=function() return 'tester' end,
    get_from=function() return {{addr='tester@EXAMPLE.test'}} end,
    get_header_raw=function() error('untrusted From header must never be used') end,
}
fixture = {format='opensigweave-domain-policy-v2',generated_at=900,
    policy_version=string.rep('a',64),
    domains={['example.test']=false, ['sub.example.test']=true}}
assert(not policy.must_tempfail(t, 'enabled', opts), 'verified Off domain must pass')
assert(not policy.must_tempfail(t, 'disabled', opts), 'Off independent of global mode')
local required = {get_user=t.get_user, get_from=function() return {{addr='tester@sub.example.test'}} end}
assert(policy.must_tempfail(required, 'disabled', opts), 'explicit required domain must tempfail')
local unknown = {get_user=t.get_user, get_from=function() return {{addr='tester@unknown.test'}} end}
assert(policy.must_tempfail(unknown, 'disabled', opts), 'unknown must never be exempt')
local unrelated = {get_user=t.get_user, get_from=function() return {{addr='tester@otherexample.test'}} end}
assert(policy.must_tempfail(unrelated, 'disabled', opts), 'no suffix inheritance')
local unauth = {get_user=function() return nil end,get_from=function() error('unsafe') end}
assert(policy.must_tempfail(unauth, 'disabled', opts), 'unauthenticated is not an exemption')
clock = 1000 + 45 * 86400
assert(not policy.must_tempfail(t, 'enabled', opts), 'trusted v2 Off must survive >24 hours and Rspamd reloading')
clock = 1000
opts.domain_policy_force_required = {'EXAMPLE.test'}
assert(policy.must_tempfail(t, 'disabled', opts), 'emergency strict override defeats stale Off')
opts.domain_policy_force_required = {'other.test'}
assert(not policy.must_tempfail(t, 'enabled', opts), 'strict override must be exact')
opts.domain_policy_force_required = {'*.example.test'}
assert(policy.must_tempfail(t, 'disabled', opts), 'invalid emergency override fails closed')
opts.domain_policy_force_required = nil
fixture.domains['bad name.test']=false
assert(policy.must_tempfail(t, 'disabled', opts), 'malformed entry invalidates snapshot')
fixture.domains['bad name.test']=nil
fixture.policy_version='garbled'
assert(policy.must_tempfail(t, 'disabled', opts), 'invalid policy version invalidates snapshot')
fixture.policy_version=string.rep('a',64)
file_available=false
assert(policy.must_tempfail(t, 'disabled', opts), 'missing policy must fail closed')
file_available=true
-- Backward compatibility: v1 still honors expiry; v2 is required for indefinite offline use.
fixture = {format='opensigweave-domain-policy-v1',generated_at=900,expires_at=1900,
  domains={['example.test']=false}}
assert(not policy.must_tempfail(t, 'disabled', opts), 'legacy active policy')
clock = 1901
assert(policy.must_tempfail(t, 'disabled', opts), 'legacy expired must fail closed')
assert(not policy.must_tempfail(t, 'disabled', {}), 'legacy global disabled')
assert(policy.must_tempfail(t, 'enabled', {}), 'legacy global enabled')
io.open, os.time = saved_open, saved_time
io.stderr:write('Offline domain policy decisions passed\n')
