-- MIME-safe multipart rewrite: use Rspamd's parsed MIME parts, not body regex.
local rspamd_util = require "rspamd_util"
local M = {}
local MAX_MESSAGE_BYTES = 20 * 1024 * 1024

local function newline(task)
    local t = task:get_newlines_type()
    if t == "cr" then return "\r" end
    if t == "lf" then return "\n" end
    return "\r\n"
end

-- Update text part metadata only. Rspamd already exposes an ordered, parsed
-- MIME-header list, so do not attempt to parse its opaque raw-header buffer
-- ourselves. Preserve every original field except the two metadata fields that
-- must change when the body is re-encoded.
local function rewritten_part_headers(part, is_html, nl)
    if type(part.get_headers) ~= "function" then
        return nil, "parsed MIME headers unavailable"
    end
    local headers = part:get_headers()
    if type(headers) ~= "table" then
        return nil, "parsed MIME headers unavailable"
    end

    local result = {}
    for _, header in ipairs(headers) do
        if type(header) ~= "table" or type(header.name) ~= "string" or header.name == "" then
            return nil, "invalid parsed MIME header"
        end
        local field = header.name:lower()
        if field ~= "content-type" and field ~= "content-transfer-encoding" then
            -- In Rspamd's FULL header representation, raw is the complete
            -- original header field (name, colon, separator, value and any
            -- folding), not merely the value. Reuse it verbatim whenever
            -- available so Content-ID/disposition/custom fields stay intact.
            local serialized = header.raw
            if type(serialized) ~= "string" or serialized == "" then
                local value = header.value
                if value == nil then value = header.decoded end
                if type(value) ~= "string" then
                    return nil, "invalid parsed MIME header value"
                end
                local separator = ""
                if header.tab_separated then
                    separator = "\t"
                elseif not header.empty_separator then
                    separator = " "
                end
                serialized = header.name .. ":" .. separator .. value
            end
            result[#result + 1] = serialized:gsub("[\r\n]+$", "")
        end
    end

    result[#result + 1] = "Content-Type: text/" .. (is_html and "html" or "plain") .. "; charset=utf-8"
    result[#result + 1] = "Content-Transfer-Encoding: quoted-printable"
    return table.concat(result, nl) .. nl
end

local function is_protected(part)
    if part:is_message() then return true end
    local major, minor = part:get_type()
    major, minor = (major or ""):lower(), (minor or ""):lower()
    if major == "multipart" and (minor == "signed" or minor == "encrypted") then return true end
    if major == "application" and (minor == "pkcs7-mime" or minor == "x-pkcs7-mime"
        or minor == "pgp-encrypted" or minor == "pkcs7-signature" or minor == "pgp-signature") then return true end
    return false
end

local function close_boundaries(emit, stack, target)
    local found = false
    for _, boundary in ipairs(stack) do
        if boundary == target then found = true; break end
    end
    if not found then return false end
    while #stack > 0 and stack[#stack] ~= target do
        emit("--" .. table.remove(stack) .. "--")
    end
    return true
end

-- transform(decoded_utf8, is_html) returns rewritten UTF-8 content or nil.
-- Returns a complete message as chunks for task:set_message, or nil + reason.
function M.rewrite(task, transform)
    local parts = task:get_parts() or {}
    if #parts == 0 then return nil, "no MIME parts" end
    if task:get_size() > MAX_MESSAGE_BYTES then return nil, "message exceeds MIME rewrite limit" end
    for _, part in ipairs(parts) do
        if is_protected(part) then return nil, "signed, encrypted or embedded message" end
        if part:is_multipart() and not part:get_boundary() then
            return nil, "multipart container without parsed boundary"
        end
    end
    local nl = newline(task)
    local header = task:get_raw_headers()
    if not header then return nil, "missing message headers" end
    local chunks = { tostring(header):gsub("[\r\n]+$", "") .. nl .. nl }
    local function emit(content, add_nl)
        chunks[#chunks + 1] = content
        if add_nl ~= false then chunks[#chunks + 1] = nl end
    end
    local stack, active, changed = {}, nil, 0
    for index, part in ipairs(parts) do
        local boundary = part:get_boundary()
        if part:is_multipart() then
            if active then emit("--" .. stack[#stack]) end
            stack[#stack + 1] = boundary
            active = boundary
            if index > 1 then
                local headers = part:get_raw_headers()
                if headers and #headers > 0 then emit(headers) end
            end
        else
            if boundary then
                if active and boundary ~= active then
                    if not close_boundaries(emit, stack, boundary) then
                        return nil, "inconsistent MIME boundary tree"
                    end
                end
                active = boundary
                emit("--" .. boundary)
            end
            local major, minor = part:get_type()
            major, minor = (major or ""):lower(), (minor or ""):lower()
            local eligible = not part:is_attachment() and major == "text"
                and (minor == "plain" or minor == "html") and part:is_text()
            if eligible then
                local text_part = part:get_text()
                local raw_utf = text_part and text_part:get_content("raw_utf")
                if not raw_utf then return nil, "text part cannot be decoded safely" end
                local original = tostring(raw_utf)
                local updated = transform(original, minor == "html")
                if type(updated) ~= "string" then return nil, "text transform failed" end
                if updated ~= original then
                    local part_headers, err = rewritten_part_headers(part, minor == "html", nl)
                    if not part_headers then return nil, err end
                    local encoded = rspamd_util.encode_qp(updated, 76, task:get_newlines_type())
                    if not encoded then return nil, "quoted-printable encoding failed" end
                    emit(part_headers)
                    emit(encoded)
                    emit("")
                    changed = changed + 1
                else
                    emit(part:get_raw_headers())
                    emit(part:get_raw_content(), false)
                end
            else
                emit(part:get_raw_headers())
                emit(part:get_raw_content(), false)
            end
        end
    end
    while #stack > 0 do emit("--" .. table.remove(stack) .. "--") end
    if changed == 0 then return nil, "no editable text was changed" end
    return chunks, changed
end

return M
