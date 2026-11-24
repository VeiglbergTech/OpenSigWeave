local rspamd_logger = require "rspamd_logger"
local rspamd_http = require "rspamd_http"
local ucl = require "ucl"
local rspamd_util = require "rspamd_util"
-- Mailcow mounts custom Lua files under /etc/rspamd/lua.
-- Extend module search path without replacing Rspamd's own library paths.
package.path = package.path .. ";/etc/rspamd/lua/?.lua"
local mime_rewriter = require "opensigweave_mime"
local cid_rewriter = require "opensigweave_cid"

-- ==========================================
-- OPENSIGWEAVE CONFIGURATION
-- ==========================================
-- Runtime configuration is supplied by the Rspamd configuration section:
-- opensigweave { api_url = "..."; engine_api_key = "..."; timeout = 5.0; }
local module_options = rspamd_config:get_all_opt("opensigweave") or {}
local ENGINE_API_KEY = module_options.engine_api_key
local API_URL_BASE = module_options.api_url
local REQUEST_TIMEOUT = tonumber(module_options.timeout) or 5.0
local INLINE_IMAGE_MODE = module_options.inline_images or "external"
if INLINE_IMAGE_MODE ~= "external" and INLINE_IMAGE_MODE ~= "cid" then
    rspamd_logger.errx(rspamd_config, "OpenSigWeave: invalid inline_images option; adapter disabled")
    return
end
local MAX_RESPONSE_SIZE = tonumber(module_options.max_response_size)
    or (INLINE_IMAGE_MODE == "cid" and 6291456 or 65536)
-- Optional policy; Postfix must also use an appropriate Milter failure action.
local FAILURE_POLICY = module_options.failure_policy or "fail_open"
if FAILURE_POLICY ~= "fail_open" and FAILURE_POLICY ~= "tempfail" then
    rspamd_logger.errx(rspamd_config, "OpenSigWeave: invalid failure_policy; adapter disabled")
    return
end
local function technical_failure(task, reason)
    local failure_id = rspamd_util.random_hex(8)
    rspamd_logger.errx(task, "OpenSigWeave failure_id=%s reason=%s policy=%s",
        failure_id, reason, FAILURE_POLICY)
    if FAILURE_POLICY == "tempfail" then
        task:set_pre_result("soft reject",
            "OpenSigWeave: Signatur konnte nicht eingefuegt werden; bitte spaeter erneut versuchen. Fehler-ID: "
                .. failure_id, "opensigweave", nil, 3)
    end
end

-- Keep remote calls bounded even if an invalid value is supplied in configuration.
REQUEST_TIMEOUT = math.min(math.max(REQUEST_TIMEOUT, 0.5), 15.0)
MAX_RESPONSE_SIZE = math.min(math.max(math.floor(MAX_RESPONSE_SIZE), 1024),
    INLINE_IMAGE_MODE == "cid" and 8388608 or 1048576)

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
    -- SOGo also emits reply attribution after <br> or a non-breaking space.
    '<[bB][rR][^>]*>%s*Am%s+[^<]-schrieb%s+',
    '<[bB][rR][^>]*>%s*&nbsp;%s*Am%s+[^<]-schrieb%s+',
    '<[dD][iI][vV][^>]*>%s*&nbsp;%s*Am%s+[^<]-schrieb%s+',
    '<[pP][^>]*>%s*&nbsp;%s*Am%s+[^<]-schrieb%s+',
    -- SOGo places German reply attribution in its own element before the
    -- quoted blockquote. Inserting at blockquote would put our signature
    -- after "Am ... schrieb ..." and break reply chronology.
    '<[dD][iI][vV][^>]*>%s*Am%s+[^<]-schrieb%s+',
    '<[pP][^>]*>%s*Am%s+[^<]-schrieb%s+',
    '<[sS][pP][aA][nN][^>]*>%s*Am%s+[^<]-schrieb%s+',
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
    '\r?\n[ \t]*Am[^\r\n]-schrieb%s+[^\r\n]-:',
    '\r?\nLe .- a écrit%s*:',
    '\r?\nEl .- escribió:',
    '\r?\n_+?\r?\nFrom: .-\r?\nTo: ',
    '\r?\nFrom: .-\r?\nTo: ',
    '\r?\n".-" %S+@%S+',
    '\r?\n> '
}

local function remove_deprecated_selector_markers(html)
    -- Remove only the control marker, never the containing element or its user content.
    html = html:gsub("<!%-%-%s*OpenSigWeave:MODE=[%a]+%s*%-%->", "")
    html = html:gsub("%s+data%-opensigweave%-mode%s*=%s*[\"'][%a]+[\"']", "")
    return html
end

local function find_signature_text_marker(task, is_reply)
    local unique_markers = {}
    local unique_count = 0
    local total_found = 0
    local duplicate_within_part = false
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
                local part_found = 0
                for name, code in string.gmatch(current, "<%-%-%-%[(.-)%]%-%-%[(.-)%]%-%-%->") do
                    part_found = part_found + 1
                    total_found = total_found + 1
                    -- multipart/alternative normally contains the same visible
                    -- marker once in text/plain and once in text/html. Treat
                    -- those as one selector, while still rejecting two markers
                    -- in the same representation or conflicting selectors.
                    local key = name .. "\0" .. code
                    if not unique_markers[key] then
                        unique_markers[key] = { name = name, code = code }
                        unique_count = unique_count + 1
                    end
                end
                if part_found > 1 then duplicate_within_part = true end
            end
        end
    end
    if total_found == 0 then
        if marker_prefix_found then return { cleanup = true, valid = false, name = "", code = "" } end
        return nil
    end
    local marker = nil
    for _, item in pairs(unique_markers) do
        marker = item
        break
    end
    marker.raw = "<---[" .. marker.name .. "]--[" .. marker.code .. "]--->"
    marker.cleanup = true
    marker.cleanup_all = total_found > 1
    marker.valid = unique_count == 1 and not duplicate_within_part
        and marker.name ~= "" and #marker.name <= 255
        and #marker.code == 4 and marker.code:match("^[%w][%w][%w][%w]$") ~= nil
    if not marker.valid then
        -- Send an explicitly invalid selector so a malformed marker cannot fall back silently.
        marker.name = ""
        marker.code = ""
        rspamd_logger.warnx(task, "OpenSigWeave: malformed or ambiguous signature text marker")
    end
    return marker
end

local function inject_signature(task)
    local envfrom = task:get_from(1)
    local uname = task:get_user()
    if type(envfrom) ~= "table" or type(envfrom[1]) ~= "table"
        or type(envfrom[1].addr) ~= "string" or envfrom[1].addr == ""
        or type(uname) ~= "string" or uname == "" then return false end
    local eligible, reason = mime_rewriter.preflight(task)
    if not eligible then
        rspamd_logger.infox(task, "OpenSigWeave: skipped unsafe mail (%s)", reason)
        return false
    end
    local original_headers = task:get_raw_headers()
    if original_headers and #original_headers > 256 * 1024 then
        rspamd_logger.warnx(task, "OpenSigWeave: header block exceeds processing limit")
        return false
    end

    local sender_email = tostring(envfrom[1].addr):lower()
    local subject = (type(task.get_subject) == "function" and task:get_subject())
        or task:get_header_raw("Subject")
    local is_forward = subject and tostring(subject):lower():match("^%s*fw[d]?:") ~= nil
    -- Forwards have no In-Reply-To header in many clients. Detect their
    -- subject so quote boundaries receive the same placement safeguards.
    local is_reply = task:get_header_raw('in-reply-to') ~= nil
        or task:get_header_raw('references') ~= nil
    local has_quoted_history = is_reply or is_forward

    -- Only authenticated submissions reach this point (task:get_user() is required above).
    -- A deprecated mode selector is ignored, but stripped so it cannot leak downstream.
    local deprecated_mode_header_count = task:get_header_count('X-OpenSigWeave-Mode')
    if deprecated_mode_header_count > 0 then
        task:modify_header('X-OpenSigWeave-Mode', { remove = { 0 } })
    end
    local signature_marker = find_signature_text_marker(task, has_quoted_history)

    local function remove_text_marker_from_message()
        if not signature_marker or not signature_marker.cleanup then return end
        -- Never mutate multipart boundaries or attachments with raw string replacement.
        local original_ct = task:get_header_raw("Content-Type")
        if original_ct and tostring(original_ct):lower():match("^%s*multipart/") then
            rspamd_logger.warnx(task, "OpenSigWeave: skipping raw marker cleanup for multipart MIME")
            return
        end
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
            local quoted_printable = signature_marker.raw:gsub("<", "=3C"):gsub(">", "=3E")
            repeat
                remaining, removed = remove_literal_once(remaining, quoted_printable)
                changed = changed or removed
            until not removed or not signature_marker.cleanup_all
            body = remaining
        else
            local cleaned, count = body:gsub("<%-%-%-%[[^\r\n]*", "")
            local escaped_cleaned, escaped_count = cleaned:gsub("&[lL][tT];%-%-%-%[[^\r\n]*", "")
            local qp_cleaned, qp_count = escaped_cleaned:gsub("=3[Cc]%-%-%-%[[^\r\n]*", "")
            if count + escaped_count + qp_count > 0 then body = qp_cleaned; changed = true end
        end
        if changed then
            task:set_message(headers:gsub("[\r\n]+$", "") .. "\r\n\r\n" .. body)
        end
    end


    local function fail_api(reason)
        if FAILURE_POLICY == "fail_open" then remove_text_marker_from_message() end
        technical_failure(task, reason)
    end

    local inline_assets = nil
    local cid_related_type = nil
    local function set_checked_message(message)
        if INLINE_IMAGE_MODE == "cid" and inline_assets and #inline_assets > 0 then
            local ok_cid, wrapped, related_type = pcall(
                cid_rewriter.wrap, task, message, inline_assets)
            if not ok_cid or not wrapped then
                technical_failure(task, "CID_MIME_REWRITE_FAILED")
                return false
            end
            message = wrapped
            cid_related_type = related_type
        end
        local ok, applied = pcall(task.set_message, task, message)
        if not ok or applied ~= true then
            technical_failure(task, "MESSAGE_MUTATION_FAILED")
            return false
        end
        rspamd_logger.infox(task, "OpenSigWeave processing_success adapter=rspamd")
        return true
    end

    local function http_callback(err, code, body, headers)
        if err or code ~= 200 then fail_api("ENGINE_HTTP_FAILURE"); return end
        if type(body) ~= "string" then fail_api("ENGINE_INVALID_RESPONSE"); return end
        local ok_parser, parser = pcall(ucl.parser)
        if not ok_parser or not parser then fail_api("ENGINE_INVALID_RESPONSE"); return end
        local ok_parse, parsed = pcall(parser.parse_string, parser, body)
        if not ok_parse or not parsed then fail_api("ENGINE_INVALID_RESPONSE"); return end
        local ok_data, data = pcall(parser.get_object, parser)
        if not ok_data or type(data) ~= "table" then fail_api("ENGINE_INVALID_RESPONSE"); return end
        local html_sig = data.html
        if INLINE_IMAGE_MODE == "cid" and data.inline_assets ~= nil
            and type(data.inline_assets) ~= "table" then
            fail_api("ENGINE_INVALID_INLINE_IMAGES")
            return
        end
        if type(html_sig) ~= "string" or type(data.inject_on_replies) ~= "boolean"
            or (data.trim_whitespace ~= nil and type(data.trim_whitespace) ~= "boolean")
            or (data.strip_device_signatures ~= nil and type(data.strip_device_signatures) ~= "boolean") then
            fail_api("ENGINE_INVALID_RESPONSE")
            return
        end

        if INLINE_IMAGE_MODE == "cid" then
            inline_assets = data.inline_assets or {}
            if #inline_assets > 6 then fail_api("ENGINE_INVALID_INLINE_IMAGES"); return end
        end
        local inject_on_replies = data.inject_on_replies
        local trim_whitespace = data.trim_whitespace
        local strip_device_signatures = data.strip_device_signatures
        
        if trim_whitespace == nil then trim_whitespace = true end
        if strip_device_signatures == nil then strip_device_signatures = false end


        local content_type = task:get_header_raw("Content-Type")
        local multipart_message = content_type and tostring(content_type):lower():match("^%s*multipart/") ~= nil
        if not html_sig or html_sig == "" then
            remove_text_marker_from_message()
            return
        end

        if is_reply and not inject_on_replies then
            remove_text_marker_from_message()
            rspamd_logger.infox(task, "OpenSigWeave: Reply detected, API flag is FALSE. Skipping.")
            return
        end
        local function html_signature_to_plain(html)
            local plain = html
            plain = plain:gsub("<[aA]%s+([^>]*)>(.-)</[aA]>", function(attrs, label)
                local href = attrs:match('[Hh][Rr][Ee][Ff]%s*=%s*"([^"]+)"')
                    or attrs:match("[Hh][Rr][Ee][Ff]%s*=%s*'([^']+)'")
                local text = label:gsub("<[^>]+>", "")
                text = text:gsub("&nbsp;", " "):gsub("&amp;", "&"):gsub("&lt;", "<"):gsub("&gt;", ">")
                if not href or href == "" then return text end
                local lower_href = href:lower()
                if lower_href:sub(1, 7) == "mailto:" then
                    local target = href:sub(8)
                    if text == "" then return target end
                    if text:lower() == target:lower() then return text end
                    return text .. " (" .. target .. ")"
                end
                if text == "" or text == href then return href end
                return text .. " (" .. href .. ")"
            end)
            return plain:gsub("<[bB][rR][^>]*>", "\n")
                :gsub("<[pP][^>]*>", "\n")
                :gsub("<[lL][iI][^>]*>", "\n- ")
                :gsub("<[^>]+>", "")
                :gsub("&nbsp;", " ")
                :gsub("&amp;", "&")
                :gsub("&lt;", "<")
                :gsub("&gt;", ">")
        end

        local plain_sig = html_signature_to_plain(html_sig)

        -- Use Rspamd's MIME/charset decoding for flat messages as well.
        -- Hand-decoding base64/quoted-printable is insufficient for ISO-8859
        -- and malformed transfer encodings must not be passed through as text.
        local function decoded_flat_body(expect_html)
            local text_parts = task:get_text_parts() or {}
            if #text_parts ~= 1 or text_parts[1]:is_html() ~= expect_html then
                return nil
            end
            local utf = text_parts[1]:get_content("raw_utf")
            return utf and tostring(utf) or nil
        end

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

        local function first_quote_boundary(payload, splitters)
            local earliest = nil
            for _, pattern in ipairs(splitters) do
                local position = string.find(payload, pattern)
                if position and (not earliest or position < earliest) then earliest = position end
            end
            return earliest
        end

        local function splice_body_logic(decoded_payload, splitters, signature, is_html, quote_hint)
            local spliced = decoded_payload
            local success = false

            -- A reprocessed mail that already carries the actual selected
            -- signature in its newly authored portion must not get a second
            -- identical copy. Quoted history alone never suppresses insertion.
            local probe = signature:gsub("^%s+", ""):gsub("%s+$", "")
            if #probe >= 12 then
                local authored_end = quote_hint or first_quote_boundary(spliced, splitters) or (#spliced + 1)
                if spliced:sub(1, authored_end - 1):find(probe, 1, true) then
                    rspamd_logger.infox(task, "OpenSigWeave: selected signature already present in authored content")
                    return decoded_payload, false
                end
            end

            -- A validly-shaped text marker is replaced at its exact position.
            -- The API has already verified its four-character SHA-256 check.
            if signature_marker and signature_marker.valid then
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
                        replaced = replaced:gsub("\r?\n[Ss]ent from my [^\r\n<]+", "")
                        replaced = replaced:gsub("\r?\n[Gg]et Outlook for [^\r\n<]+", "")
                        replaced = replaced:gsub("<[bB][rR]%s*/?>[Ss]ent from my [^<]+", "")
                        replaced = replaced:gsub("<[bB][rR]%s*/?>[Gg]et Outlook for [^<]+", "")
                    end
                    replaced = is_html and clean_trailing_html(replaced) or clean_trailing_plain(replaced)
                    rspamd_logger.infox(task, "OpenSigWeave: replaced explicit signature text marker")
                    return replaced, true
                end
            elseif signature_marker and signature_marker.cleanup then
                local removed = false
                if signature_marker.raw then
                    local first, last
                    repeat
                        first, last = string.find(spliced, signature_marker.raw, 1, true)
                        if first then
                            spliced = string.sub(spliced, 1, first - 1) .. string.sub(spliced, last + 1)
                            removed = true
                        end
                    until not first or not signature_marker.cleanup_all
                    if is_html then
                        local escaped = signature_marker.raw:gsub("<", "&lt;"):gsub(">", "&gt;")
                        local a, b
                        repeat
                            a, b = string.find(spliced, escaped, 1, true)
                            if a then spliced = string.sub(spliced, 1, a - 1) .. string.sub(spliced, b + 1); removed = true end
                        until not a or not signature_marker.cleanup_all
                    end
                else
                    local cleaned, count = spliced:gsub("<%-%-%-%[[^\r\n]*", "")
                    local escaped_cleaned, escaped_count = cleaned:gsub("&[lL][tT];%-%-%-%[[^\r\n]*", "")
                    local qp_cleaned, qp_count = escaped_cleaned:gsub("=3[Cc]%-%-%-%[[^\r\n]*", "")
                    spliced = qp_cleaned
                    removed = count + escaped_count + qp_count > 0
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

            if has_quoted_history then
                local best_s = quote_hint or first_quote_boundary(spliced, splitters)

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

        local raw_body_data = task:get_rawbody()
        local raw_body = raw_body_data and tostring(raw_body_data) or ""
        
        local raw_headers_data = task:get_raw_headers()
        local raw_headers = raw_headers_data and tostring(raw_headers_data) or ""
        
        local ct_raw = task:get_header_raw('Content-Type')
        local ct_header = ct_raw and tostring(ct_raw):lower() or ""
        
        local is_multipart = ct_header:match("^%s*multipart/") ~= nil
        local is_flat_plain = not is_multipart and (ct_header == "" or string.find(ct_header, "text/plain") ~= nil)
        local is_flat_html = not is_multipart and string.find(ct_header, "text/html", 1, true) ~= nil

        if is_flat_plain then
            rspamd_logger.errx(task, "OpenSigWeave: Upgrading Flat text/plain to multipart/alternative.")
            
            local enc_raw = task:get_header_raw('Content-Transfer-Encoding')
            local encoding = (enc_raw and tostring(enc_raw) or "7bit"):lower()
            
            local decoded = decoded_flat_body(false)
            if not decoded then
                technical_failure(task, "FLAT_TEXT_DECODE_FAILED")
                return
            end

            decoded = decoded:gsub("^[%s\r\n]+", ""):gsub("[%s\r\n]+$", "")

            local spliced_plain, _ = splice_body_logic(decoded, plain_splitters, plain_sig, false)

            local html_prefix = "<!DOCTYPE html>\n<html>\n<body>\n"
            local function escape_plain_as_html(text)
                return text:gsub("&", "&amp;"):gsub("<", "&lt;"):gsub(">", "&gt;"):gsub("\r?\n", "<br>\n")
            end
            -- Keep both alternatives aligned. Detect the quote in the *original*
            -- plain reply before HTML escaping changes its structure.
            local plain_quote = first_quote_boundary(decoded, plain_splitters)
            local html_quote = nil
            local escaped_body
            if plain_quote then
                local authored_html = escape_plain_as_html(decoded:sub(1, plain_quote - 1))
                escaped_body = authored_html .. escape_plain_as_html(decoded:sub(plain_quote))
                html_quote = #html_prefix + #authored_html + 1
            else
                escaped_body = escape_plain_as_html(decoded)
            end
            local html_body = html_prefix .. escaped_body .. "\n</body>\n</html>"
            local spliced_html, _ = splice_body_logic(html_body, html_splitters, html_sig, true, html_quote)

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
            if not set_checked_message(full_msg) then return end
            
            local reply_ok = pcall(task.set_milter_reply, task, {
                remove_headers = {
                    ['Content-Type'] = 0,
                    ['Content-Transfer-Encoding'] = 0
                },
                add_headers = {
                    ['Content-Type'] = { value = cid_related_type or ('multipart/alternative; boundary="' .. boundary .. '"'), order = 1 },
                    ['Content-Transfer-Encoding'] = { value = '7bit', order = 1 }
                }
            })
            if not reply_ok then technical_failure(task, "MILTER_REPLY_FAILED") end
            
        elseif is_flat_html then
            local enc_raw = task:get_header_raw('Content-Transfer-Encoding')
            local encoding = (enc_raw and tostring(enc_raw) or "7bit"):lower()
            local decoded = decoded_flat_body(true)
            if not decoded then
                technical_failure(task, "FLAT_HTML_DECODE_FAILED")
                return
            end
            decoded = remove_deprecated_selector_markers(decoded)
            local spliced_html, _ = splice_body_logic(decoded, html_splitters, html_sig, true)
            local encoded_html = tostring(rspamd_util.encode_qp(spliced_html, 76) or spliced_html)
            local kept_headers, dropping_cte = {}, false
            for line in raw_headers:gmatch("([^\n]*\n?)") do
                if line == "" then break end
                if line:match("^[A-Za-z0-9%-]+:") then
                    local field = line:lower()
                    dropping_cte = field:match("^content%-transfer%-encoding:") ~= nil
                        or field:match("^content%-type:") ~= nil
                end
                if not dropping_cte then kept_headers[#kept_headers + 1] = line end
            end
            local new_headers = table.concat(kept_headers):gsub("[\r\n]+$", "")
            if not set_checked_message(new_headers .. "\r\nContent-Type: text/html; charset=utf-8\r\n"
                .. "Content-Transfer-Encoding: quoted-printable\r\n\r\n" .. encoded_html) then return end
            local reply_ok = pcall(task.set_milter_reply, task, {
                remove_headers = { ['Content-Type'] = 0, ['Content-Transfer-Encoding'] = 0 },
                add_headers = {
                    ['Content-Type'] = { value = cid_related_type or 'text/html; charset=utf-8', order = 1 },
                    ['Content-Transfer-Encoding'] = { value = cid_related_type and '7bit' or 'quoted-printable', order = 1 }
                }
            })
            if not reply_ok then technical_failure(task, "MILTER_REPLY_FAILED") end
        elseif is_multipart then
            -- Rspamd parses MIME boundaries; only decoded text is passed to the
            -- existing reply-placement algorithm. Attachments are unchanged.
            local already_present = false
            local rewrite_ok, message, changed_or_reason = pcall(mime_rewriter.rewrite, task, function(decoded, is_html)
                if is_html then decoded = remove_deprecated_selector_markers(decoded) end
                local rewritten, inserted = splice_body_logic(decoded,
                    is_html and html_splitters or plain_splitters,
                    is_html and html_sig or plain_sig, is_html)
                if not inserted and rewritten == decoded then already_present = true end
                return rewritten
            end)
            if not rewrite_ok then
                technical_failure(task, "MULTIPART_REWRITE_EXCEPTION")
            elseif message then
                if not set_checked_message(message) then return end
                -- The Rspamd Milter replaces the message body separately from
                -- envelope headers. When CID wraps an existing multipart root,
                -- the outer Content-Type must follow that new MIME structure.
                if cid_related_type then
                    local reply_ok, applied = pcall(task.set_milter_reply, task, {
                        remove_headers = { ['Content-Type'] = 0 },
                        add_headers = {
                            ['Content-Type'] = { value = cid_related_type, order = 1 }
                        }
                    })
                    if not reply_ok or applied == false then
                        technical_failure(task, "CID_MILTER_HEADER_UPDATE_FAILED")
                        return
                    end
                end
                rspamd_logger.infox(task, "OpenSigWeave: rewritten %s multipart MIME text parts", changed_or_reason)
            elseif changed_or_reason ~= "no editable text was changed" or not already_present then
                technical_failure(task, "MULTIPART_REWRITE_FAILED")
            else
                rspamd_logger.infox(task, "OpenSigWeave: existing signature retained")
            end
        else
            technical_failure(task, "UNSUPPORTED_MESSAGE_TYPE")
        end
    end

    -- Rspamd invokes this after the asynchronous HTTP request; exceptions here
    -- would otherwise bypass the strict failure policy and permit unsigned mail.
    local function guarded_http_callback(err, code, body, headers)
        local ok = pcall(http_callback, err, code, body, headers)
        if not ok then
            technical_failure(task, "ENGINE_CALLBACK_EXCEPTION")
        end
    end

    local request_headers = { ['X-Engine-Key'] = ENGINE_API_KEY }
    if INLINE_IMAGE_MODE == "cid" then
        request_headers['X-OpenSigWeave-Inline-Images'] = "cid"
    end
    if signature_marker then
        request_headers['X-OpenSigWeave-Signature'] = signature_marker.name or ""
        request_headers['X-OpenSigWeave-Signature-Code'] = signature_marker.code or ""
    end

    local request_ok, scheduled = pcall(rspamd_http.request, {
        url = API_URL_BASE .. url_encode(sender_email),
        task = task,
        method = 'GET',
        headers = request_headers,
        timeout = REQUEST_TIMEOUT,
        max_size = MAX_RESPONSE_SIZE,
        no_ssl_verify = false,
        callback = guarded_http_callback
    })

    if not request_ok or not scheduled then
        if FAILURE_POLICY == "fail_open" then remove_text_marker_from_message() end
        technical_failure(task, "ENGINE_REQUEST_NOT_SCHEDULED")
    end
    return true
end

-- Guard synchronous prefilter errors too. Authenticate before reporting a failure:
-- inbound unauthenticated mail must never inherit the submission-only policy.
local function guarded_inject_signature(task)
    local auth_ok, user = pcall(task.get_user, task)
    if not auth_ok or type(user) ~= "string" or user == "" then
        return false
    end
    local ok, result = pcall(inject_signature, task)
    if not ok then
        technical_failure(task, "INJECTION_EXCEPTION")
        return false
    end
    return result
end

rspamd_config:register_symbol({
    name = 'OPENSIGWEAVE_INJECT',
    type = 'prefilter',
    callback = guarded_inject_signature,
    priority = 0
})
