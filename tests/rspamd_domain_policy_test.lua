-- Isolated Rspamd policy decisions using a trusted fixture parser and file.
package.path = 'rspamd/?.lua;' .. package.path
local fixture = nil
local file_available = true
local payload = '{}'
local saved_open = io.open
local saved_time = os.time
io.open = function(path, mode)
    if not file_available or path ~= '/tmp/osw-policy-fixture.json' then return nil end
    return {read=function(_, bytes) return payload end, close=function() end}
end
os.time = function() return 1000 end
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
fixture = {format='opensigweave-domain-policy-v1',generated_at=900,expires_at=1900,
    domains={['example.test']=false, ['sub.example.test']=true}}
assert(policy.must_tempfail(t, 'enabled', opts) == false, 'verified optional domain must fail open')
assert(policy.must_tempfail(t, 'disabled', opts) == false, 'global enabled cannot override optional exemption')
local required = {get_user=t.get_user, get_from=function() return {{addr='tester@sub.example.test'}} end}
assert(policy.must_tempfail(required, 'disabled', opts), 'explicit required domain must tempfail')
local unknown = {get_user=t.get_user, get_from=function() return {{addr='tester@unknown.test'}} end}
assert(policy.must_tempfail(unknown, 'disabled', opts), 'unknown domain must never be exempt')
local unrelated = {get_user=t.get_user, get_from=function() return {{addr='tester@otherexample.test'}} end}
assert(policy.must_tempfail(unrelated, 'disabled', opts), 'exact match, no suffix inheritance')
local unauth = {get_user=function() return nil end,get_from=function() error('not safe') end}
assert(policy.must_tempfail(unauth, 'disabled', opts), 'unknown authenticated status is not an exemption')
fixture.expires_at=999
assert(policy.must_tempfail(t, 'disabled', opts), 'expired snapshot must fail closed')
fixture.expires_at=1900
fixture.domains['bad name.test']=false
assert(policy.must_tempfail(t, 'disabled', opts), 'malformed entry invalidates complete snapshot')
fixture.domains['bad name.test']=nil
file_available=false
assert(policy.must_tempfail(t, 'disabled', opts), 'missing policy must fail closed')
file_available=true
assert(not policy.must_tempfail(t, 'disabled', {}), 'legacy mode still respects global disabled')
assert(policy.must_tempfail(t, 'enabled', {}), 'legacy enabled still rejects')
io.open = saved_open
os.time = saved_time
io.stderr:write('Offline domain policy decisions passed\n')
