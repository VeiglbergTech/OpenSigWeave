-- Add bounded CID image parts without changing the bytes of existing MIME leaves.
-- The first related part is the fully rendered original email MIME body.
local rspamd_util = require "rspamd_util"
local M = {}
local MAX_IMAGES = 6
local MAX_ENCODED_BYTES = 6 * 1024 * 1024
local MAX_MESSAGE_BYTES = 20 * 1024 * 1024

local function newline(task)
    local kind = task:get_newlines_type()
    if kind == "cr" then return "\r" end
    if kind == "lf" then return "\n" end
    return "\r\n"
end

local function safe_asset(asset)
    if type(asset) ~= "table" then return false end
    local filename = asset.filename
    local cid = asset.content_id
    local media = asset.content_type
    local encoded = asset.base64
    if type(filename) ~= "string" or type(cid) ~= "string"
        or type(media) ~= "string" or type(encoded) ~= "string" then return false end
    local digest, extension = filename:match("^([a-f0-9]+)%.([a-z]+)$")
    if not digest or #digest ~= 64 then return false end
    local types = { png = "image/png", jpg = "image/jpeg",
        gif = "image/gif", webp = "image/webp" }
    if media ~= types[extension] then return false end
    -- The engine binds each CID to the actual immutable content-addressed file.
    if cid ~= ("osw-" .. digest .. "@assets.opensigweave.invalid") then return false end
    if #encoded == 0 or #encoded > 3 * 1024 * 1024
        or not encoded:match("^[A-Za-z0-9+/]+=?=?$") or #encoded % 4 ~= 0 then return false end
    return true
end

function M.wrap(task, message, assets)
    if type(assets) ~= "table" or #assets == 0 or #assets > MAX_IMAGES then
        return nil, "invalid number of inline assets"
    end
    if type(message) == "table" then
        -- Rspamd's multipart rewriter returns mixed Lua strings and
        -- rspamd_text userdata (raw headers, encoded bodies, attachments).
        -- table.concat() rejects userdata even though tostring() preserves
        -- the exact binary-safe bytes of rspamd_text.
        local chunks, total = {}, 0
        for _, chunk in ipairs(message) do
            local kind = type(chunk)
            if kind ~= "string" and kind ~= "userdata" then
                return nil, "invalid outgoing MIME chunk"
            end
            if kind == "userdata" then
                local ok, raw = pcall(tostring, chunk)
                if not ok or type(raw) ~= "string" then
                    return nil, "invalid outgoing MIME chunk"
                end
                chunk = raw
            end
            total = total + #chunk
            if total > MAX_MESSAGE_BYTES then
                return nil, "outgoing message exceeds MIME limit"
            end
            chunks[#chunks + 1] = chunk
        end
        message = table.concat(chunks)
    end
    if type(message) ~= "string" then return nil, "missing outgoing message" end
    local nl = newline(task)
    local split = message:find(nl .. nl, 1, true)
    if not split then return nil, "missing message header terminator" end
    local original_headers = message:sub(1, split - 1)
    local body = message:sub(split + 2 * #nl)
    local outer, inner = {}, {}
    local seen_ct, seen_mime = false, false
    local destination = nil
    for line in (original_headers .. nl):gmatch("(.-)" .. nl) do
        if line:match("^[ \t]") then
            if not destination or #destination == 0 then return nil, "invalid folded MIME header" end
            destination[#destination] = destination[#destination] .. nl .. line
        else
            local name = line:match("^([%w%-]+):")
            if not name then return nil, "invalid outgoing MIME headers" end
            name = name:lower()
            if name == "content-type" then
                if seen_ct then return nil, "duplicate top-level MIME content type" end
                seen_ct = true
            elseif name == "mime-version" then
                seen_mime = true
            end
            if name:match("^content%-") then destination = inner else destination = outer end
            destination[#destination + 1] = line
        end
    end
    if not seen_ct then return nil, "missing top-level MIME content type" end
    local parts = type(task.get_parts) == "function" and (task:get_parts() or {}) or {}
    if #parts + #assets + 1 > 256 then return nil, "too many MIME parts" end

    local size = 0
    local unique = {}
    for _, asset in ipairs(assets) do
        if not safe_asset(asset) then return nil, "invalid managed CID image" end
        if unique[asset.content_id:lower()] then return nil, "duplicate content ID" end
        unique[asset.content_id:lower()] = true
        size = size + #asset.base64
        if size > MAX_ENCODED_BYTES then return nil, "inline assets exceed limit" end
    end
    -- Real Rspamd mimepart:get_headers() returns opaque rspamd_text, NOT
    -- an array of header fields. Query Content-ID directly through the
    -- documented mimepart:get_header_raw() API instead.
    for _, part in ipairs(parts) do
        if type(part.get_header_raw) == "function" then
            local count = type(part.get_header_count) == "function"
                and part:get_header_count("Content-ID") or 0
            if count and count > 1 then return nil, "duplicate existing content ID headers" end
            local raw = part:get_header_raw("Content-ID")
            if raw ~= nil then
                local value = tostring(raw):gsub("^%s+", ""):gsub("%s+$", "")
                local id = value:match("^<([^<>]+)>$") or value:match("^(%S+)$")
                if not id then return nil, "invalid existing content ID" end
                if unique[id:lower()] then return nil, "existing content ID collision" end
            end
        elseif type(part.get_headers) == "function" then
            -- Standalone Lua tests or older mocks may expose a parsed table;
            -- never assume a real Rspamd MIME part has that representation.
            local headers = part:get_headers()
            if type(headers) ~= "table" then return nil, "parsed MIME headers unavailable" end
            for _, header in ipairs(headers) do
                if type(header) == "table" and type(header.name) == "string"
                    and header.name:lower() == "content-id" then
                    local value = header.value or header.decoded
                    if type(value) ~= "string" then return nil, "invalid existing content ID" end
                    local id = value:match("<([^>]+)>") or value:match("^%s*(%S+)%s*$")
                    if id and unique[id:lower()] then return nil, "existing content ID collision" end
                end
            end
        end
    end
    for _, header in ipairs(inner) do
        if header:lower():match("^content%-id:") then
            local id = header:match("<([^>]+)>")
            if id and unique[id] then return nil, "existing content ID collision" end
        end
    end
    if #message + size + 4096 + #assets * 1024 > MAX_MESSAGE_BYTES then
        return nil, "outgoing message exceeds MIME limit"
    end
    if not seen_mime then outer[#outer + 1] = "MIME-Version: 1.0" end

    local boundary = "----OSWCID" .. rspamd_util.random_hex(16)
    local related_ct = 'multipart/related; boundary="' .. boundary .. '"'
    outer[#outer + 1] = "Content-Type: " .. related_ct
    local chunks = {
        table.concat(outer, nl), nl, nl,
        "--" .. boundary, nl,
        table.concat(inner, nl), nl, nl,
        body
    }
    if body:sub(-#nl) ~= nl then chunks[#chunks + 1] = nl end
    for _, asset in ipairs(assets) do
        -- Lua patterns do not support {76}; construct RFC 2045 compliant
        -- base64 lines explicitly (maximum 76 printable characters per line).
        local lines = {}
        for offset = 1, #asset.base64, 76 do
            lines[#lines + 1] = asset.base64:sub(offset, offset + 75)
        end
        local encoded = table.concat(lines, nl)
        chunks[#chunks + 1] = "--" .. boundary .. nl
        chunks[#chunks + 1] = "Content-Type: " .. asset.content_type .. nl
        chunks[#chunks + 1] = "Content-Disposition: inline; filename=\"" .. asset.filename .. "\"" .. nl
        chunks[#chunks + 1] = "Content-Transfer-Encoding: base64" .. nl
        chunks[#chunks + 1] = "Content-ID: <" .. asset.content_id .. ">" .. nl .. nl
        chunks[#chunks + 1] = encoded
        if encoded:sub(-#nl) ~= nl then chunks[#chunks + 1] = nl end
    end
    chunks[#chunks + 1] = "--" .. boundary .. "--" .. nl
    return table.concat(chunks), related_ct
end

return M
