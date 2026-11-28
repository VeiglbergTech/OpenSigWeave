-- Local, read-only domain failure policy. Never query OpenSigWeave in the
-- technical failure path: its API may be exactly what is unavailable.
-- The local snapshot is delivered by an authenticated TLS sync or by a
-- trusted administrator. No root-owned configuration is modified by Rspamd.
local ucl = require "ucl"
local M = {}
local MAX_BYTES = 65536
local MAX_AGE = 86400

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
    -- Only trusted authenticated envelope sender information is used. Do not
    -- use the human-readable, potentially forged From: message header.
    local ok_user, user = pcall(task.get_user, task)
    if not ok_user or type(user) ~= "string" or user == "" then return nil end
    local ok_from, envelope = pcall(task.get_from, task, 1)
    if not ok_from or type(envelope) ~= "table" or type(envelope[1]) ~= "table" then
        return nil
    end
    local addr = envelope[1].addr
    if type(addr) ~= "string" then return nil end
    local domain = addr:match("^[^@]+@([^@]+)$")
    return normalized_domain(domain)
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
    if not ok_data or type(data) ~= "table" then return nil end
    if data.format ~= "opensigweave-domain-policy-v1"
        or type(data.generated_at) ~= "number"
        or type(data.expires_at) ~= "number"
        or type(data.domains) ~= "table" then return nil end
    local now = os.time()
    if data.generated_at > now + 300 or data.generated_at < 0
        or data.expires_at <= now or data.expires_at <= data.generated_at
        or data.expires_at > data.generated_at + MAX_AGE then return nil end
    -- Validate every domain entry: reject the whole policy if any malformed
    -- exemption could otherwise be accepted following a partial update.
    local count = 0
    for name, required in pairs(data.domains) do
        count = count + 1
        if count > 10000 or normalized_domain(name) ~= name
            or type(required) ~= "boolean" then return nil end
    end
    return data.domains
end

function M.must_tempfail(task, global_failure_policy, options)
    local file = options and options.domain_policy_file
    if file == nil then
        -- Legacy installations with no offline domain policy keep the
        -- historical global failure-policy behavior.
        return global_failure_policy ~= "disabled"
    end
    -- Explicitly selecting offline domain policy enables strict fallback:
    -- missing/expired/corrupt snapshot or unknown sender must never become
    -- an unverified exemption (even with global failure_policy=disabled).
    local domain = sender_domain(task)
    if not domain then return true end
    local known = read_policy(file)
    if not known then return true end
    return known[domain] ~= false
end

return M
