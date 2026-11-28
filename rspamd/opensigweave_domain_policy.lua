-- Offline, read-only authenticated-submission domain policy.
-- Trust comes from an authenticated HTTPS sync and a root-controlled file;
-- policy_version is a fingerprint, NOT an independent digital signature.
local ucl = require "ucl"
-- Use Rspamd's built-in crypto implementation; do not rely on an unchecked
-- version string in a manually edited or damaged file.
local hash_available, hash_module = pcall(require, "rspamd_cryptobox_hash")
local M = {}
local MAX_BYTES = 65536
local LEGACY_MAX_AGE = 86400

local function normalized_domain(raw)
    if type(raw) ~= "string" or #raw < 3 or #raw > 253 then return nil end
    local name = raw:lower():gsub("%.$", "")
    if not name:find(".", 1, true) or name:find("[^a-z0-9.%-]") then return nil end
    if name:find("%.%.") or name:sub(1,1) == "." or name:sub(-1) == "." then return nil end
    for label in name:gmatch("[^.]+") do
        if #label < 1 or #label > 63 or label:sub(1,1) == "-"
            or label:sub(-1) == "-" then return nil end
    end
    return name
end

local function sender_domain(task)
    -- Only authenticated SMTP envelope sender, never a message From: header.
    local ok_user, user = pcall(task.get_user, task)
    if not ok_user or type(user) ~= "string" or user == "" then return nil end
    local ok_from, envelope = pcall(task.get_from, task, 1)
    if not ok_from or type(envelope) ~= "table" or type(envelope[1]) ~= "table" then
        return nil
    end
    local addr = envelope[1].addr
    if type(addr) ~= "string" then return nil end
    return normalized_domain(addr:match("^[^@]+@([^@]+)$"))
end

local function check_envelope(data, now)
    if type(data) ~= "table" or type(data.domains) ~= "table"
        or type(data.generated_at) ~= "number"
        or data.generated_at ~= math.floor(data.generated_at)
        or data.generated_at <= 0 or data.generated_at > now + 300 then return false end
    local expected = nil
    if data.format == "opensigweave-domain-policy-v2" then
        expected = {format=true, generated_at=true, policy_version=true, domains=true}
        if type(data.policy_version) ~= "string" or #data.policy_version ~= 64
            or data.policy_version:find("[^a-f0-9]") then return false end
        -- Unlike v1, a verified local v2 exemption has no time expiry.
    elseif data.format == "opensigweave-domain-policy-v1" then
        expected = {format=true, generated_at=true, expires_at=true, domains=true}
        if type(data.expires_at) ~= "number"
            or data.expires_at <= now or data.expires_at <= data.generated_at
            or data.expires_at > data.generated_at + LEGACY_MAX_AGE then return false end
    else
        return false
    end
    for key, _ in pairs(data) do if not expected[key] then return false end end
    for key, _ in pairs(expected) do if data[key] == nil then return false end end
    local count = 0
    for name, required in pairs(data.domains) do
        count = count + 1
        if count > 10000 or normalized_domain(name) ~= name
            or type(required) ~= "boolean" then return false end
    end
    if data.format == "opensigweave-domain-policy-v2" then
        -- Domain names contain only [a-z0-9.-], so this matches the
        -- canonical JSON produced by Python json.dumps(sort_keys=True,
        -- separators=(",", ":"), ensure_ascii=True) without JSON escaping.
        local names = {}
        for name, _ in pairs(data.domains) do names[#names + 1] = name end
        table.sort(names)
        local fields = {}
        for _, name in ipairs(names) do
            fields[#fields + 1] = '"' .. name .. '":'
                .. (data.domains[name] and "true" or "false")
        end
        local canonical = "{" .. table.concat(fields, ",") .. "}"
        if not hash_available or type(hash_module.create_specific) ~= "function" then
            return false
        end
        local ok, digest = pcall(function()
            local sha = hash_module.create_specific("sha256")
            sha:update(canonical)
            return sha:hex()
        end)
        if not ok or digest ~= data.policy_version then return false end
    end
    return true
end

local function read_policy(path)
    if type(path) ~= "string" or path:sub(1,1) ~= "/"
        or path:find("..", 1, true) or #path > 512 then return nil end
    local ok, file = pcall(io.open, path, "rb")
    if not ok or not file then return nil end
    local ok_read, raw = pcall(file.read, file, MAX_BYTES + 1)
    pcall(file.close, file)
    if not ok_read or type(raw) ~= "string" or #raw > MAX_BYTES then return nil end
    local ok_parser, parser = pcall(ucl.parser)
    if not ok_parser or not parser then return nil end
    local ok_parse, parsed = pcall(parser.parse_string, parser, raw)
    if not ok_parse or not parsed then return nil end
    local ok_data, data = pcall(parser.get_object, parser)
    if not ok_data or not check_envelope(data, os.time()) then return nil end
    return data.domains
end

local function emergency_requires(domain, options)
    -- Root-administered local strict overrides take precedence over stale Off
    -- policies. A malformed override fails closed rather than being ignored.
    local strict = options and options.domain_policy_force_required
    if strict == nil then return false end
    if type(strict) == "string" then strict = {strict} end
    if type(strict) ~= "table" then return true end
    local count = 0
    for key, name in pairs(strict) do
        count = count + 1
        if count > 10000 or type(key) ~= "number"
            or type(name) ~= "string" or normalized_domain(name) == nil then return true end
        if normalized_domain(name) == domain then return true end
    end
    return false
end

function M.must_tempfail(task, global_failure_policy, options)
    local file = options and options.domain_policy_file
    if file == nil then
        -- No opt-in keeps historical enabled/disabled behavior.
        return global_failure_policy ~= "disabled"
    end
    local domain = sender_domain(task)
    if not domain then return true end
    if emergency_requires(domain, options) then return true end
    local known = read_policy(file)
    if not known then return true end
    -- Exact case-insensitive match. Unknown domains never inherit exemptions.
    return known[domain] ~= false
end

return M
