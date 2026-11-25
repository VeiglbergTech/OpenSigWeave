-- Separate, opt-in Rspamd guard for a missing OpenSigWeave injection plugin.
-- Install and configure independently of opensigweave.lua; the guard cannot
-- help if its own configuration, Rspamd or the Postfix Milter link is bypassed.
local rspamd_logger = require "rspamd_logger"
local rspamd_util = require "rspamd_util"

local options = rspamd_config:get_all_opt("opensigweave_guard") or {}
if options.enabled == true then
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

        local failure_id = rspamd_util.random_hex(8)
        rspamd_logger.errx(
            task, "OpenSigWeave guard failure_id=%s reason=SIGNATURE_PLUGIN_NOT_LOADED",
            failure_id
        )
        task:set_pre_result(
            "soft reject",
            "OpenSigWeave: Signaturmodul nicht geladen; bitte spaeter erneut versuchen. Fehler-ID: " .. failure_id,
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
