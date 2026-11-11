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

    local function http_callback(err, code, body, headers)
        if err or code ~= 200 then
            rspamd_logger.errx(task, "OpenSigWeave: API error (%s)", err or code)
            return
        end

        local parser = ucl.parser()
        local res, ucl_err = parser:parse_string(body)
        if not res then return end
        
        local data = parser:get_object()
        if type(data) ~= "table" then return end
        local html_sig = data.html
        if type(html_sig) ~= "string" then return end
        
        local inject_on_replies = data.inject_on_replies
        local trim_whitespace = data.trim_whitespace
        local strip_device_signatures = data.strip_device_signatures
        
        if trim_whitespace == nil then trim_whitespace = true end
        if strip_device_signatures == nil then strip_device_signatures = false end

        if not html_sig or html_sig == "" then return end

        if is_reply and not inject_on_replies then
            rspamd_logger.infox(task, "OpenSigWeave: Reply detected, API flag is FALSE. Skipping.")
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
