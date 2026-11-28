-- Separate, opt-in Rspamd guard for a missing OpenSigWeave injection plugin.
-- Install and configure independently of opensigweave.lua; the guard cannot
-- help if its own configuration, Rspamd or the Postfix Milter link is bypassed.
local rspamd_logger = require "rspamd_logger"
local rspamd_util = require "rspamd_util"
package.path = package.path .. ";/etc/rspamd/lua/?.lua"
local options = rspamd_config:get_all_opt("opensigweave_guard") or {}
local adapter_options = rspamd_config:get_all_opt("opensigweave") or {}
local domain_policy = nil
local domain_policy_unavailable = false
if adapter_options.domain_policy_file ~= nil then
    local loaded, candidate = pcall(require, "opensigweave_domain_policy")
    if loaded and type(candidate) == "table" and type(candidate.must_tempfail) == "function" then
        domain_policy = candidate
    else
        domain_policy_unavailable = true
        rspamd_logger.errx(rspamd_config,
            "OpenSigWeave guard: configured offline domain policy module is unavailable; defaulting to temporary rejection")
    end
end
local failure_policy = adapter_options.failure_policy or "disabled"
if failure_policy == "fail_open" then failure_policy = "disabled" end
if failure_policy == "tempfail" or failure_policy == "standard" then failure_policy = "enabled" end
-- A disabled failure policy must not turn a missing optional plugin into SMTP
-- rejection. A missing guard cannot independently enforce the policy.
if options.enabled == true and
    (failure_policy ~= "disabled" or adapter_options.domain_policy_file ~= nil) then
    local function require_injector(task)
        -- Never reject unauthenticated inbound SMTP for missing outbound plugin.
        local user_ok, user = pcall(task.get_user, task)
        if not user_ok or type(user) ~= "string" or user == "" then
            return false
        end

        -- The late postfilter stage is reached after normal prefilter registration.
        -- A configured, reachable Rspamd Milter can otherwise silently accept
        -- unsigned mail when opensigweave.lua is absent or fails to load.
        local lookup_ok, callback = pcall(
            rspamd_config.get_symbol_callback, rspamd_config, "OPENSIGWEAVE_INJECT"
        )
        if lookup_ok and type(callback) == "function" then
            return false
        end

        local defer = domain_policy_unavailable or
            (domain_policy and domain_policy.must_tempfail(task, failure_policy, adapter_options)) or
            (not domain_policy and failure_policy ~= "disabled")
        if not defer then return false end
        local failure_id = rspamd_util.random_hex(8)
        rspamd_logger.errx(
            task, "OpenSigWeave guard failure_id=%s reason=SIGNATURE_PLUGIN_NOT_LOADED",
            failure_id
        )
        task:set_pre_result(
            "soft reject",
            "OpenSigWeave: Your email could not be sent because the signature service is unavailable. Please try again later. Error ID: " .. failure_id,
            "opensigweave_guard",
            nil,
            3
        )
        return true
    end

    rspamd_config:register_symbol({
        name = "OPENSIGWEAVE_GUARD",
        type = "postfilter",
        callback = require_injector,
        priority = 0,
        flags = "empty,nostat",
    })
end
