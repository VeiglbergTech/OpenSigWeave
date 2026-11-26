-- CI fixture: the separately registered guard must reject authenticated mail
-- when the injection callback is missing, but never affect incoming messages.
local errors = {}
package.preload["rspamd_logger"] = function()
    return {
        errx = function(_, fmt, id)
            errors[#errors + 1] = string.format(fmt, id)
        end
    }
end
package.preload["rspamd_util"] = function()
    return { random_hex = function(n) assert(n == 8); return "0011aabb" end }
end

local registered
local injector
local enabled = true
local failure_policy = "standard"
rspamd_config = {
    get_all_opt = function(_, name)
        if name == "opensigweave" then return { failure_policy = failure_policy } end
        assert(name == "opensigweave_guard")
        return { enabled = enabled }
    end,
    get_symbol_callback = function(_, name)
        assert(name == "OPENSIGWEAVE_INJECT")
        return injector
    end,
    register_symbol = function(_, symbol)
        registered = symbol
    end,
}

local function task_case(user)
    local status = {}
    local task = {
        get_user = function() return user end,
        set_pre_result = function(_, action, message, module, _, priority)
            status.action = action
            status.message = message
            status.module = module
            status.priority = priority
        end
    }
    return task, status
end

dofile("rspamd/opensigweave_guard.lua")
assert(registered and registered.name == "OPENSIGWEAVE_GUARD")
assert(registered.type == "postfilter")
assert(registered.flags == "empty,nostat")
local check = registered.callback

local inbound, inbound_result = task_case(nil)
assert(check(inbound) == false and not inbound_result.action,
    "unauthenticated inbound mail must be untouched")

local plugin_missing, missing_result = task_case("test@example.org")
assert(check(plugin_missing) == true)
assert(missing_result.action == "soft reject", "missing plugin must tempfail")
assert(missing_result.message:find("Error ID: 0011aabb", 1, true))
assert(missing_result.module == "opensigweave_guard")
assert(missing_result.priority == 3)
assert(#errors == 1 and errors[1]:find("SIGNATURE_PLUGIN_NOT_LOADED", 1, true))

injector = function() end
local healthy, healthy_result = task_case("test@example.org")
assert(check(healthy) == false and not healthy_result.action,
    "loaded signature plugin must not be blocked")

rspamd_config.get_symbol_callback = function() error("symbol table unavailable") end
local lookup_error, lookup_result = task_case("test@example.org")
assert(check(lookup_error) == true and lookup_result.action == "soft reject",
    "unable to verify registration must tempfail")

enabled = false
registered = nil
dofile("rspamd/opensigweave_guard.lua")
assert(registered == nil, "guard must remain opt-in")

enabled = true
failure_policy = "disabled"
registered = nil
dofile("rspamd/opensigweave_guard.lua")
assert(registered == nil, "disabled mode must never register rejection guard")
failure_policy = "standard"

io.stderr:write("OpenSigWeave independent guard fixture passed\n")
