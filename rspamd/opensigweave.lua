local rspamd_logger = require "rspamd_logger"
local rspamd_http = require "rspamd_http"
local ucl = require "ucl"
local rspamd_util = require "rspamd_util"

-- ==========================================
-- OPENSIGWEAVE CONFIGURATION
-- ==========================================
-- Runtime configuration is supplied by the Rspamd configuration section:
-- opensigweave { api_url = "..."; engine_api_key = "..."; timeout = 5.0; }
local module_options = rspamd_config:get_all_opt("opensigweave") or {}
local ENGINE_API_KEY = module_options.engine_api_key
local API_URL_BASE = module_options.api_url
local REQUEST_TIMEOUT = tonumber(module_options.timeout) or 5.0
local MAX_RESPONSE_SIZE = tonumber(module_options.max_response_size) or 65536

-- Keep remote calls bounded even if an invalid value is supplied in configuration.
REQUEST_TIMEOUT = math.min(math.max(REQUEST_TIMEOUT, 0.5), 15.0)
MAX_RESPONSE_SIZE = math.min(math.max(math.floor(MAX_RESPONSE_SIZE), 1024), 1048576)

if type(ENGINE_API_KEY) ~= "string" or #ENGINE_API_KEY < 32 or ENGINE_API_KEY:match("^[Rr][Ee][Pp][Ll][Aa][Cc][Ee]_")
    or type(API_URL_BASE) ~= "string" or not API_URL_BASE:match("^https://") then
    rspamd_logger.errx(rspamd_config, "OpenSigWeave disabled: configure an HTTPS api_url and a unique engine_api_key (minimum 32 characters)")
    return
end

if API_URL_BASE:sub(-1) ~= "/" then
    API_URL_BASE = API_URL_BASE .. "/"
end

local function url_encode(value)
    return (value:gsub("([^%w%-_%.~])", function(char)
        return string.format("%%%02X", string.byte(char))
    end))
end

local html_splitters = {
    '<br[^>]*>[%s\r\n]*<br[^>]*>[%s\r\n]*<br[^>]*>[^<]*On .- wrote:',
    '<br[^>]*>[%s\r\n]*<br[^>]*>[^<]*On .- wrote:',
    '<br[^>]*>[%s\r\n]*[^<]*On .- wrote:',
    '<p[^>]*>&nbsp;</p>[%s\r\n]*On .- wrote:',
    '<hr[^>]*>[%s\r\n]*<b>From:</b>',
    '<div[^>]*>[%s\r\n]*<hr[^>]*>[%s\r\n]*<b>From:</b>',
    '<div class="gmail_quote"', '<div class=3D"gmail_quote"',
    '<div class="quote"', '<div class=3D"quote"',
    '<blockquote[^>]*type="cite"', '<blockquote[^>]*type=3D"cite"',
    '<div class="moz%-cite%-prefix">', '<div class=3D"moz%-cite%-prefix">',
    '<hr tabindex="%-%d+"', '<hr tabindex=3D"%-%d+"',
    '<div id="divRplyFwdMsg"', '<div id=3D"divRplyFwdMsg"',
    '<div style="border:none;border%-top:solid', '<div style=3D"border:none;border%-top:solid',
    '<div data%-marker="__QUOTED_TEXT__"', '<div data%-marker=3D"__QUOTED_TEXT__"',
    '<div class="yahoo_quoted"', '<div class=3D"yahoo_quoted"',
    '<blockquote id="isReplyContent"', '<blockquote id=3D"isReplyContent"',
    '<span id="OLK_SRC_BODY_SECTION"', '<span id=3D"OLK_SRC_BODY_SECTION"',
    '<div class="ox-[0-9a-f]+-quote"', '<div class=3D"ox-[0-9a-f]+-quote"'
}

local plain_splitters = {
    '\r?\n%-+ Original Message %-+',
    '\r?\nOn .- wrote:',
    '\r?\nAm .- schrieb .-*:',
    '\r?\nLe .- a écrit%s*:',
    '\r?\nEl .- escribió:',
    '\r?\n_+?\r?\nFrom: .-\r?\nTo: ',
    '\r?\nFrom: .-\r?\nTo: ',
    '\r?\n".-" %S+@%S+',
    '\r?\n> '
}

local function find_outlook_mode_marker(task, is_reply)
    local parts = task:get_text_parts() or {}
    local found_modes = {}
    for _, part in ipairs(parts) do
        if part:is_html() then
            local content_data = part:get_content("raw_utf")
            local html = content_data and tostring(content_data) or ""
            if html ~= "" then
                -- A marker must occur in the newest message section, never solely in quoted history.
                local quote_start = #html + 1
                for _, pattern in ipairs(html_splitters) do
                    local start_pos = string.find(html, pattern)
                    if start_pos and start_pos < quote_start then quote_start = start_pos end
                end
                -- On replies, fail closed if no known quote boundary can be identified.
                if not (is_reply and quote_start == #html + 1) then
                local current_html = string.sub(html, 1, quote_start - 1)
                for value in string.gmatch(current_html, "<!%-%-%s*OpenSigWeave:MODE=([%a]+)%s*%-%->") do
                    found_modes[string.upper(value)] = true
                end
                local lower_html = string.lower(current_html)
                for value in string.gmatch(lower_html, "data%-opensigweave%-mode%s*=%s*[\"']([%a]+)[\"']") do
                    found_modes[string.upper(value)] = true
                end
                end
            end
        end
    end

    local selected = nil
    local count = 0
    for value in pairs(found_modes) do
        if value == "AUTO" or value == "PROFESSIONAL" or value == "INTERNAL" or value == "NONE" then
            selected = value
            count = count + 1
        else
            return nil
        end
    end
    if count == 1 then return selected end
    if count > 1 then
        rspamd_logger.warnx(task, "OpenSigWeave: ignoring conflicting Outlook mode markers")
    end
    return nil
end

local function remove_outlook_mode_markers(html)
    -- Remove only the control marker, never the containing element or its user content.
    html = html:gsub("<!%-%-%s*OpenSigWeave:MODE=[%a]+%s*%-%->", "")
    html = html:gsub("%s+data%-opensigweave%-mode%s*=%s*[\"'][%a]+[\"']", "")
    return html
end

local function find_signature_text_marker(task, is_reply)
    local found = {}
    local marker_prefix_found = false
    local parts = task:get_text_parts() or {}
    for _, part in ipairs(parts) do
        local data = part:get_content("raw_utf")
        local content = data and tostring(data) or ""
        if content ~= "" then
            local is_html = part:is_html()
            local quote_start = #content + 1
            local splitters = is_html and html_splitters or plain_splitters
            for _, pattern in ipairs(splitters) do
                local start_pos = string.find(content, pattern)
                if start_pos and start_pos < quote_start then quote_start = start_pos end
            end
            if not (is_reply and quote_start == #content + 1) then
                local current = string.sub(content, 1, quote_start - 1)
                -- HTML clients may encode angle brackets in text nodes.
                current = current:gsub("&lt;", "<"):gsub("&gt;", ">")
                if current:find("<%-%-%-%[") then marker_prefix_found = true end
                for name, code in string.gmatch(current, "<%-%-%-%[(.-)%]%-%-%[(.-)%]%-%-%->") do
                    found[#found + 1] = { name = name, code = code }
                end
            end
        end
    end
    if #found == 0 then
        if marker_prefix_found then return { cleanup = true, valid = false } end
        return nil
    end
    local marker = found[1]
    marker.raw = "<---[" .. marker.name .. "]--[" .. marker.code .. "]--->"
    marker.cleanup = true
    marker.cleanup_all = #found > 1
    marker.valid = #found == 1 and marker.name ~= "" and #marker.name <= 255
        and #marker.code == 4 and marker.code:match("^[%w][%w][%w][%w]$") ~= nil
    if not marker.valid then
        rspamd_logger.warnx(task, "OpenSigWeave: malformed or ambiguous signature text marker")
    end
    return marker
end

local function inject_signature(task)
    local envfrom = task:get_from(1)
    local uname = task:get_user()
    if not envfrom or not uname then return false end

    local sender_email = tostring(envfrom[1].addr):lower()
    local is_reply = task:get_header_raw('in-reply-to') ~= nil or task:get_header_raw('references') ~= nil

    -- Only authenticated submissions reach this point (task:get_user() is required above).
    -- Consume the control header so it cannot leak to recipients or be replayed downstream.
    local requested_mode = nil
    local mode_header_count = task:get_header_count('X-OpenSigWeave-Mode')
    if mode_header_count == 1 then
        local raw_mode = task:get_header('X-OpenSigWeave-Mode')
        if type(raw_mode) == 'string' then
            local candidate = raw_mode:gsub("^%s+", ""):gsub("%s+$", "")
            candidate = candidate:upper()
            if candidate == 'AUTO' or candidate == 'PROFESSIONAL'
                or candidate == 'INTERNAL' or candidate == 'NONE' then
                requested_mode = candidate
            else
                rspamd_logger.warnx(task, "OpenSigWeave: ignoring unsupported mode header value")
            end
        end
    elseif mode_header_count > 1 then
        rspamd_logger.warnx(task, "OpenSigWeave: ignoring duplicate mode headers")
    end
    if mode_header_count > 0 then
        task:modify_header('X-OpenSigWeave-Mode', { remove = { 0 } })
    end
    -- X-OpenSigWeave-Mode has strict precedence over both body marker formats.
    -- Always inspect the body so a text marker can be consumed even when an
    -- X-header has priority. Invalid X-headers simply fall through to the next selector.
    local signature_marker = find_signature_text_marker(task, is_reply)
    if not requested_mode then
        if not signature_marker or not signature_marker.valid then
            requested_mode = find_outlook_mode_marker(task, is_reply)
        end
    end

    local function remove_text_marker_from_message()
        if not signature_marker or not signature_marker.cleanup then return end
        local body_data = task:get_rawbody()
        local headers_data = task:get_raw_headers()
        local body = body_data and tostring(body_data) or ""
        local headers = headers_data and tostring(headers_data) or ""
        local function remove_literal_once(value, literal)
            if not literal or literal == "" then return value, false end
            local first, last = string.find(value, literal, 1, true)
            if not first then return value, false end
            return string.sub(value, 1, first - 1) .. string.sub(value, last + 1), true
        end
        local changed = false
        if signature_marker.raw then
            local remaining = body
            local removed
            repeat
                remaining, removed = remove_literal_once(remaining, signature_marker.raw)
                changed = changed or removed
            until not removed or not signature_marker.cleanup_all
            local escaped = signature_marker.raw:gsub("<", "&lt;"):gsub(">", "&gt;")
            repeat
                remaining, removed = remove_literal_once(remaining, escaped)
                changed = changed or removed
            until not removed or not signature_marker.cleanup_all
            body = remaining
        end
        if changed then
            task:set_message(headers:gsub("[\r\n]+$", "") .. "\r\n\r\n" .. body)
        end
    end


    local function http_callback(err, code, body, headers)
        if err or code ~= 200 then
            remove_text_marker_from_message()
            rspamd_logger.errx(task, "OpenSigWeave: API error (%s)", err or code)
            return
        end

        local parser = ucl.parser()
        local res, ucl_err = parser:parse_string(body)
        if not res then remove_text_marker_from_message(); return end
        
        local data = parser:get_object()
        if type(data) ~= "table" then remove_text_marker_from_message(); return end
        local html_sig = data.html
        if type(html_sig) ~= "string" then remove_text_marker_from_message(); return end
        
        local inject_on_replies = data.inject_on_replies
        local trim_whitespace = data.trim_whitespace
        local strip_device_signatures = data.strip_device_signatures
        
        if trim_whitespace == nil then trim_whitespace = true end
        if strip_device_signatures == nil then strip_device_signatures = false end


        local content_type = task:get_header_raw("Content-Type")
        local multipart_message = content_type and tostring(content_type):lower():find("multipart", 1, true) ~= nil
        if not html_sig or html_sig == "" then
            remove_text_marker_from_message()
            return
        end

        if is_reply and not inject_on_replies then
            remove_text_marker_from_message()
            rspamd_logger.infox(task, "OpenSigWeave: Reply detected, API flag is FALSE. Skipping.")
            return
        end
        if multipart_message then
            remove_text_marker_from_message()
            rspamd_logger.warnx(task, "OpenSigWeave: multipart message left unchanged; safe MIME rewrite is not yet available")
            return
        end

        local plain_sig = html_sig:gsub("<br.->", "\n"):gsub("<p.->", "\n"):gsub("<li.->", "\n- "):gsub("<[^>]+>", ""):gsub("&nbsp;", " ")

        local function clean_trailing_html(html)
            if not trim_whitespace then return html end
            html = html:gsub("=\r?\n", "")
            local prev
            repeat
                prev = html
                html = html:gsub("[%s\r\n]+$", "")
                html = html:gsub("=20$", "")
                html = html:gsub("=C2=A0$", "")
                html = html:gsub("\194\160$", "")
                html = html:gsub("=$", "")
                
                html = html:gsub("<br[^>]*>[%s\r\n]*$", "")
                html = html:gsub("<br[^>]*>[%s\r\n]*</[dD][iI][vV]>$", "</div>")
                html = html:gsub("<br[^>]*>[%s\r\n]*</[pP]>$", "</p>")
                
                -- The NextCloud / Horde whitespace fixes:
                -- Catch standard empty paragraphs, HTML entity NBSP, and raw UTF-8 (\194\160) NBSP
                html = html:gsub("<[pP][^>]*>[%s\r\n]*</[pP]>$", "")
                html = html:gsub("<[pP][^>]*>[%s\r\n]*&nbsp;[%s\r\n]*</[pP]>$", "")
                html = html:gsub("<[pP][^>]*>[%s\r\n]*\194\160[%s\r\n]*</[pP]>$", "")
                
                html = html:gsub("<[dD][iI][vV][^>]*>[%s\r\n]*</[dD][iI][vV]>$", "")
                html = html:gsub("<[dD][iI][vV][^>]*>[%s\r\n]*&nbsp;[%s\r\n]*</[dD][iI][vV]>$", "")
                html = html:gsub("<[dD][iI][vV][^>]*>[%s\r\n]*\194\160[%s\r\n]*</[dD][iI][vV]>$", "")
                
                html = html:gsub("</div>[%s\r\n]*<br[^>]*>[%s\r\n]*$", "</div>")
            until html == prev
            return html
        end

        local function clean_trailing_plain(plain)
            if not trim_whitespace then return plain end
            plain = plain:gsub("=\r?\n", "")
            local prev
            repeat
                prev = plain
                plain = plain:gsub("[%s\r\n]+$", "")
                plain = plain:gsub("=20$", "")
                plain = plain:gsub("=C2=A0$", "")
                plain = plain:gsub("\194\160$", "")
                plain = plain:gsub("=$", "")
                plain = plain:gsub("\194\160[%s\r\n]*$", "")
            until plain == prev
            return plain
        end

        local function splice_body_logic(decoded_payload, splitters, signature, is_html)
            local spliced = decoded_payload
            local success = false

            -- A validly-shaped text marker is replaced at its exact position.
            -- The API has already verified its four-character SHA-256 check.
            if signature_marker and signature_marker.valid and not requested_mode then
                local marker_pattern = "<%-%-%-%[" .. signature_marker.name:gsub("([^%w])", "%%%1")
                    .. "%]%-%-%[" .. signature_marker.code .. "%]%-%-%->"
                local replaced, count = spliced:gsub(marker_pattern, function() return signature end, 1)
                if count == 0 and is_html then
                    local escaped_pattern = "&lt;---%[" .. signature_marker.name:gsub("([^%w])", "%%%1")
                        .. "%]%-%-%[" .. signature_marker.code .. "%]%-%-%-&gt;"
                    replaced, count = spliced:gsub(escaped_pattern, function() return signature end, 1)
                end
                if count > 0 then
                    if strip_device_signatures then
                        replaced = replaced:gsub("\\r?\\n[Ss]ent from my [^\\r\\n<]+", "")
                        replaced = replaced:gsub("\\r?\\n[Gg]et Outlook for [^\\r\\n<]+", "")
                        replaced = replaced:gsub("<[bB][rR]%s*/?>[Ss]ent from my [^<]+", "")
                        replaced = replaced:gsub("<[bB][rR]%s*/?>[Gg]et Outlook for [^<]+", "")
                    end
                    replaced = is_html and clean_trailing_html(replaced) or clean_trailing_plain(replaced)
                    rspamd_logger.infox(task, "OpenSigWeave: replaced explicit signature text marker")
                    return replaced, true
                end
            elseif signature_marker and signature_marker.raw then
                local removed = false
                local first, last = string.find(spliced, signature_marker.raw, 1, true)
                if first then
                    spliced = string.sub(spliced, 1, first - 1) .. string.sub(spliced, last + 1)
                    removed = true
                end
                if is_html then
                    local escaped = signature_marker.raw:gsub("<", "&lt;"):gsub(">", "&gt;")
                    local a, b = string.find(spliced, escaped, 1, true)
                    if a then spliced = string.sub(spliced, 1, a - 1) .. string.sub(spliced, b + 1); removed = true end
                end
                if removed then rspamd_logger.infox(task, "OpenSigWeave: removed unused signature text marker") end
            end

            if strip_device_signatures then
                spliced = spliced:gsub("\r?\n[Ss]ent from my [^\r\n<]+", "")
                spliced = spliced:gsub("\r?\n[Gg]et Outlook for [^\r\n<]+", "")
                spliced = spliced:gsub("<[bB][rR]%s*/?>[Ss]ent from my [^<]+", "")
                spliced = spliced:gsub("<[bB][rR]%s*/?>[Gg]et Outlook for [^<]+", "")
            end

            local html_spacer_top = '\n<br clear="all"><br>\n<div style="display:block; clear:both;">\n'
            local html_spacer_bottom = '\n</div>\n<br>\n'

            if is_reply then
                local best_s = nil
                for _, pattern in ipairs(splitters) do
                    local s, e = string.find(spliced, pattern)
                    if s and (not best_s or s < best_s) then best_s = s end
                end

                if best_s then
                    local before = string.sub(spliced, 1, best_s - 1)
                    local after = string.sub(spliced, best_s)

                    if is_html then
                        before = clean_trailing_html(before)
                        after = after:gsub("^([%s\r\n]*<[bB][rR]%s*/?>)+", "")
                        
                        local clean_sig = trim_whitespace and clean_trailing_html(signature):gsub("^[%s\r\n]+", "") or signature
                        spliced = before .. html_spacer_top .. clean_sig .. html_spacer_bottom .. after
                    else
                        before = clean_trailing_plain(before)
                        local clean_sig = trim_whitespace and clean_trailing_plain(signature):gsub("^[%s\r\n]+", "") or signature
                        after = trim_whitespace and after:gsub("^[%s\r\n]+", "") or after
                        
                        spliced = before .. "\r\n\r\n" .. clean_sig .. "\r\n\r\n" .. after
                    end
                    success = true
                    rspamd_logger.infox(task, "OpenSigWeave: Successfully injected inline reply signature.")
                end
            end

            if not success then
                if is_html then
                    local last_body_s = nil
                    for s in string.gmatch(spliced, "()</[bB][oO][dD][yY]>") do last_body_s = s end

                    if last_body_s then
                        local before = string.sub(spliced, 1, last_body_s - 1)
                        local after = string.sub(spliced, last_body_s)
                        before = clean_trailing_html(before)
                        local clean_sig = trim_whitespace and clean_trailing_html(signature):gsub("^[%s\r\n]+", "") or signature
                        
                        spliced = before .. html_spacer_top .. clean_sig .. html_spacer_bottom .. after
                    else
                        local clean_sig = trim_whitespace and clean_trailing_html(signature):gsub("^[%s\r\n]+", "") or signature
                        spliced = clean_trailing_html(spliced) .. html_spacer_top .. clean_sig .. html_spacer_bottom
                    end
                else
                    local clean_sig = trim_whitespace and clean_trailing_plain(signature):gsub("^[%s\r\n]+", "") or signature
                    spliced = clean_trailing_plain(spliced) .. "\r\n\r\n" .. clean_sig .. "\r\n"
                end
                success = true
                rspamd_logger.infox(task, "OpenSigWeave: Successfully appended new email signature to bottom.")
            end
            return spliced, success
        end

        local function process_mime_block(raw_body_str, target_type)
            local tgt_regex = ""
            for i = 1, #target_type do
                local c = target_type:sub(i, i)
                tgt_regex = tgt_regex .. "[" .. c:upper() .. c:lower() .. "]"
            end

            local ctype_pattern = "[Cc][Oo][Nn][Tt][Ee][Nn][Tt]%-[Tt][Yy][Pp][Ee]:%s*[Tt][Ee][Xx][Tt]/" .. tgt_regex .. ".-(\r?\n\r?\n)"
            local start_idx, header_end_idx = string.find(raw_body_str, ctype_pattern)
            
            if not start_idx then 
                return raw_body_str, false 
            end

            local part_headers = string.sub(raw_body_str, start_idx, header_end_idx)
            local end_idx = string.find(raw_body_str, "\r?\n%-%-", header_end_idx)
            if not end_idx then end_idx = #raw_body_str + 1 end
            
            local raw_payload = string.sub(raw_body_str, header_end_idx + 1, end_idx - 1)

            local encoding = "7bit"
            local cte_match = string.match(part_headers:lower(), "content%-transfer%-encoding:%s*([%w%-]+)")
            if cte_match then encoding = cte_match end

            local decoded = tostring(raw_payload)
            if encoding == "base64" then
                local dec = rspamd_util.decode_base64(raw_payload)
                if dec then decoded = tostring(dec) end
            elseif encoding == "quoted-printable" then
                local dec = rspamd_util.decode_qp(raw_payload)
                if dec then decoded = tostring(dec) end
            end

            local is_html = (target_type == "html")
            if is_html then
                decoded = remove_outlook_mode_markers(decoded)
            end
            local spliced_payload, _ = splice_body_logic(decoded, is_html and html_splitters or plain_splitters, is_html and html_sig or plain_sig, is_html)

            local enc_payload = rspamd_util.encode_qp(spliced_payload, 76)
            local encoded_payload = enc_payload and tostring(enc_payload) or spliced_payload

            local stripped_headers = part_headers:gsub("\r?\n[Cc][Oo][Nn][Tt][Ee][Nn][Tt]%-[Tt][Rr][Aa][Nn][Ss][Ff][Ee][Rr]%-[Ee][Nn][Cc][Oo][Dd][Ii][Nn][Gg]:[^\r\n]*", "")
            stripped_headers = stripped_headers:gsub("\r?\n\r?\n$", "")
            local new_headers = stripped_headers .. "\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n"

            local before_body = string.sub(raw_body_str, 1, start_idx - 1)
            local after_body = string.sub(raw_body_str, end_idx)

            return before_body .. new_headers .. encoded_payload .. after_body, true
        end

        local raw_body_data = task:get_rawbody()
        local raw_body = raw_body_data and tostring(raw_body_data) or ""
        
        local raw_headers_data = task:get_raw_headers()
        local raw_headers = raw_headers_data and tostring(raw_headers_data) or ""
        
        local ct_raw = task:get_header_raw('Content-Type')
        local ct_header = ct_raw and tostring(ct_raw):lower() or ""
        
        local is_multipart = string.find(ct_header, "multipart") ~= nil
        local is_flat_plain = not is_multipart and (ct_header == "" or string.find(ct_header, "text/plain") ~= nil)
        local is_flat_html = not is_multipart and string.find(ct_header, "text/html", 1, true) ~= nil

        -- Do not rewrite raw multipart bodies with string patterns. Nested boundaries,
        -- transfer encodings and inline resources require a parser-backed rewrite path.
        -- Until that path is implemented and exercised against supported Rspamd versions,
        -- preserving the original message is safer than risking MIME corruption.
        if is_multipart then
            rspamd_logger.warnx(task, "OpenSigWeave: multipart message left unchanged; safe MIME rewrite is not yet available")
            return
        end

        if is_flat_plain then
            rspamd_logger.errx(task, "OpenSigWeave: Upgrading Flat text/plain to multipart/alternative.")
            
            local enc_raw = task:get_header_raw('Content-Transfer-Encoding')
            local encoding = (enc_raw and tostring(enc_raw) or "7bit"):lower()
            
            local decoded = tostring(raw_body)
            if encoding:find("base64") then
                local dec = rspamd_util.decode_base64(raw_body)
                if dec then decoded = tostring(dec) end
            elseif encoding:find("quoted%-printable") then
                local dec = rspamd_util.decode_qp(raw_body)
                if dec then decoded = tostring(dec) end
            end

            decoded = decoded:gsub("^[%s\r\n]+", ""):gsub("[%s\r\n]+$", "")

            local spliced_plain, _ = splice_body_logic(decoded, plain_splitters, plain_sig, false)

            local html_body = "<!DOCTYPE html>\n<html>\n<body>\n"
            local escaped_body = decoded:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"):gsub("\r?\n", "<br>\n")
            html_body = html_body .. escaped_body .. "\n</body>\n</html>"
            
            local spliced_html, _ = splice_body_logic(html_body, html_splitters, html_sig, true)

            local boundary = "----OpenSigWeaveAlt" .. rspamd_util.random_hex(12)
            
            local qp_plain = tostring(rspamd_util.encode_qp(spliced_plain, 76) or spliced_plain):gsub("[%s\r\n]+$", "")
            local qp_html = tostring(rspamd_util.encode_qp(spliced_html, 76) or spliced_html):gsub("[%s\r\n]+$", "")

            local new_multipart_body = "--" .. boundary .. "\r\n" ..
                "Content-Type: text/plain; charset=utf-8\r\n" ..
                "Content-Transfer-Encoding: quoted-printable\r\n\r\n" ..
                qp_plain .. "\r\n" ..
                "--" .. boundary .. "\r\n" ..
                "Content-Type: text/html; charset=utf-8\r\n" ..
                "Content-Transfer-Encoding: quoted-printable\r\n\r\n" ..
                qp_html .. "\r\n" ..
                "--" .. boundary .. "--\r\n"

            local new_headers_str = ""
            local skip_mode = false
            local has_ct = false
            local has_cte = false

            for line in raw_headers:gmatch("([^\n]*\n?)") do
                if line == "" then break end
                
                local is_new_header = line:match("^[A-Za-z0-9%-]+:")
                
                if is_new_header then
                    if line:match("^[Cc][Oo][Nn][Tt][Ee][Nn][Tt]%-[Tt][Yy][Pp][Ee]:") then
                        skip_mode = true
                        new_headers_str = new_headers_str .. 'Content-Type: multipart/alternative; boundary="' .. boundary .. '"\r\n'
                        has_ct = true
                    elseif line:match("^[Cc][Oo][Nn][Tt][Ee][Nn][Tt]%-[Tt][Rr][Aa][Nn][Ss][Ff][Ee][Rr]%-[Ee][Nn][Cc][Oo][Dd][Ii][Nn][Gg]:") then
                        skip_mode = true
                        new_headers_str = new_headers_str .. 'Content-Transfer-Encoding: 7bit\r\n'
                        has_cte = true
                    else
                        skip_mode = false
                        new_headers_str = new_headers_str .. line
                    end
                else
                    if not skip_mode then
                        new_headers_str = new_headers_str .. line
                    end
                end
            end

            if not has_ct then
                new_headers_str = new_headers_str .. 'Content-Type: multipart/alternative; boundary="' .. boundary .. '"\r\n'
            end
            if not has_cte then
                new_headers_str = new_headers_str .. 'Content-Transfer-Encoding: 7bit\r\n'
            end
            
            new_headers_str = new_headers_str:gsub("[\r\n]+$", "")
            
            local full_msg = new_headers_str .. "\r\n\r\n" .. new_multipart_body
            task:set_message(full_msg)
            
            task:set_milter_reply({
                remove_headers = {
                    ['Content-Type'] = 0,
                    ['Content-Transfer-Encoding'] = 0
                },
                add_headers = {
                    ['Content-Type'] = { value = 'multipart/alternative; boundary="' .. boundary .. '"', order = 1 },
                    ['Content-Transfer-Encoding'] = { value = '7bit', order = 1 }
                }
            })
            
        elseif is_flat_html then
            local enc_raw = task:get_header_raw('Content-Transfer-Encoding')
            local encoding = (enc_raw and tostring(enc_raw) or "7bit"):lower()
            local decoded = tostring(raw_body)
            if encoding:find("base64", 1, true) then
                local dec = rspamd_util.decode_base64(raw_body)
                if dec then decoded = tostring(dec) end
            elseif encoding:find("quoted-printable", 1, true) then
                local dec = rspamd_util.decode_qp(raw_body)
                if dec then decoded = tostring(dec) end
            end
            decoded = remove_outlook_mode_markers(decoded)
            local spliced_html, _ = splice_body_logic(decoded, html_splitters, html_sig, true)
            local encoded_html = tostring(rspamd_util.encode_qp(spliced_html, 76) or spliced_html)
            local new_headers = raw_headers:gsub("[Cc][Oo][Nn][Tt][Ee][Nn][Tt]%-[Tt][Rr][Aa][Nn][Ss][Ff][Ee][Rr]%-[Ee][Nn][Cc][Oo][Dd][Ii][Nn][Gg]:[^\r\n]*\r?\n", "")
            new_headers = new_headers:gsub("[\r\n]+$", "")
            task:set_message(new_headers .. "\r\nContent-Transfer-Encoding: quoted-printable\r\n\r\n" .. encoded_html)
            task:set_milter_reply({
                remove_headers = { ['Content-Transfer-Encoding'] = 0 },
                add_headers = { ['Content-Transfer-Encoding'] = { value = 'quoted-printable', order = 1 } }
            })
        else
            local html_success, plain_success = false, false
            raw_body, html_success = process_mime_block(raw_body, "html")
            raw_body, plain_success = process_mime_block(raw_body, "plain")
            
            local new_headers = raw_headers:gsub("\r?\n*$", "")
            local full_msg = new_headers .. "\r\n\r\n" .. raw_body
            
            task:set_message(full_msg)
        end
    end

    local request_headers = { ['X-Engine-Key'] = ENGINE_API_KEY }
    if requested_mode then
        request_headers['X-OpenSigWeave-Mode'] = requested_mode
    elseif signature_marker and signature_marker.valid then
        request_headers['X-OpenSigWeave-Signature'] = signature_marker.name
        request_headers['X-OpenSigWeave-Signature-Code'] = signature_marker.code
    end

    local scheduled = rspamd_http.request({
        url = API_URL_BASE .. url_encode(sender_email),
        task = task,
        method = 'GET',
        headers = request_headers,
        timeout = REQUEST_TIMEOUT,
        max_size = MAX_RESPONSE_SIZE,
        no_ssl_verify = false,
        callback = http_callback
    })

    if not scheduled then
        rspamd_logger.errx(task, "OpenSigWeave: unable to schedule signature API request")
    end
    return true
end

rspamd_config:register_symbol({
    name = 'OPENSIGWEAVE_INJECT',
    type = 'prefilter',
    callback = inject_signature,
    priority = 0
})
