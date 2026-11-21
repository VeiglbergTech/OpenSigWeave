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
    if not filename:match("^[a-f0-9]+%.(png|jpg|gif|webp)$") or #filename < 68 or #filename > 69 then
        -- Lua patterns do not implement alternation; explicit extensions below.
        local digest, extension = filename:match("^([a-f0-9]+)%.([a-z]+)$")
        if not digest or #digest ~= 64
            or (extension ~= "png" and extension ~= "jpg"
                and extension ~= "gif" and extension ~= "webp") then return false end
    end
    if not cid:match("^osw%-%x+@assets%.opensigweave%.invalid$") or #cid > 110 then return false end
    if media ~= "image/png" and media ~= "image/jpeg"
        and media ~= "image/webp" and media ~= "image/gif" then return false end
    if #encoded == 0 or #encoded > 3 * 1024 * 1024
        or not encoded:match("^[A-Za-z0-9+/=]+$") or #encoded % 4 ~= 0 then return false end
    return true
end

function M.wrap(task, message, assets)
    if type(assets) ~= "table" or #assets == 0 or #assets > MAX_IMAGES then
        return nil, "invalid number of inline assets"
    end
    if type(message) == "table" then message = table.concat(message) end
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
    local part_count = type(task.get_parts) == "function" and #(task:get_parts() or {}) or 0
    if part_count + #assets + 1 > 256 then return nil, "too many MIME parts" end

    local size = 0
    local unique = {}
    for _, asset in ipairs(assets) do
        if not safe_asset(asset) then return nil, "invalid managed CID image" end
        if unique[asset.content_id] then return nil, "duplicate content ID" end
        unique[asset.content_id] = true
        size = size + #asset.base64
        if size > MAX_ENCODED_BYTES then return nil, "inline assets exceed limit" end
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
        local encoded = asset.base64:gsub("(.{76})", "%1" .. nl)
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
