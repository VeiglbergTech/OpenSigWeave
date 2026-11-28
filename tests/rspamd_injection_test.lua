-- Execute the Rspamd plugin with mocked network/parser but genuine body logic.
package.path = 'rspamd/?.lua;' .. package.path
local callback, status = nil, 200
local failure_policy = 'fail_open'
local inline_images_mode = 'external'
local policy_file = nil
local offline_policy_fixture = nil
local request_error, request_scheduled = nil, true
local response = {
  html = '<b>OpenSigWeave Footer</b> <a href="https://example.test/contact">Contact</a>',
  inject_on_replies = true,
  trim_whitespace = true,
  strip_device_signatures = false,
}
local diagnostic_logs = {}
package.preload['rspamd_logger'] = function()
  return {errx=function(_, fmt, ...)
    if type(fmt) == 'string' and fmt:find('engine HTTP failure', 1, true) then
      diagnostic_logs[#diagnostic_logs + 1] = string.format(fmt, ...)
    end
  end, warnx=function() end, infox=function() end}
end
package.preload['rspamd_http'] = function()
  return { request=function(opts)
    if not request_scheduled then return false end
    opts.callback(request_error, status, '{}', {})
    return true
  end }
end
package.preload['rspamd_cryptobox_hash'] = function() return {
  create_specific=function(kind)
    assert(kind == 'sha256')
    local payload
    return {
      update=function(_, data) payload = data end,
      hex=function()
        if payload == '{"example.test":false,"required.test":true}' then return 'a6d05536fe3c9096fe2f3bd552ceed4f53aca57e3e1666d67b6e4b551f4ac544' end
        return string.rep('0',64)
      end,
    }
  end,
} end
package.preload['ucl'] = function()
  return { parser=function()
    local current_raw = nil
    return {
      parse_string=function(_, raw) current_raw = raw; return true end,
      get_object=function()
        if current_raw == 'OSW_OFFLINE_POLICY' then return offline_policy_fixture end
        return response
      end,
    }
  end}
end
local function qp(s)
  local output, col = {}, 0
  for i=1,#s do
    local ch, byte = s:sub(i,i), s:byte(i)
    if ch == '\r' then
      output[#output+1] = ch
    elseif ch == '\n' then
      output[#output+1] = ch
      col = 0
    else
      local token
      if (byte >= 33 and byte <= 60) or (byte >= 62 and byte <= 126) then
        token = ch
      else
        token = string.format('=%02X', byte)
      end
      if col + #token > 70 then
        output[#output+1] = '=\r\n'
        col = 0
      end
      output[#output+1] = token
      col = col + #token
    end
  end
  return table.concat(output)
end
package.preload['rspamd_util'] = function()
  return {encode_qp=qp, random_hex=function() return 'fedcba987654' end}
end
rspamd_config = {
  get_all_opt=function() return {
    api_url='https://signature.example.test/api/v1/engine/signatures/',
    engine_api_key=string.rep('x',40),
    failure_policy=failure_policy,
    inline_images=inline_images_mode,
    domain_policy_file=policy_file,
  } end,
  register_symbol=function(_,sym) callback=sym.callback end,
}
dofile('rspamd/opensigweave.lua')
assert(type(callback)=='function')

local function task_case(ct, cte, body, decoded, is_html, other_headers, reply)
  local top_headers = 'From: sender@example.test\r\nTo: receiver@example.test\r\nMIME-Version: 1.0\r\n'
    .. 'Subject: Grüße und Prüfung\r\n' .. (other_headers or '')
    .. 'Content-Type: ' .. ct .. '\r\nContent-Transfer-Encoding: ' .. cte .. '\r\n'
  local result = {modified=nil, mime_actions=nil, pre_result=nil}
  local textpart = {is_html=function() return is_html end, get_content=function(_, form)
    assert(form=='raw_utf')
    return decoded
  end}
  local part = {
    get_type=function() return 'text',is_html and 'html' or 'plain' end,
    is_message=function() return false end,
  }
  local task = {
    get_from=function() return {{addr='sender@example.test'}} end,
    get_user=function() return 'sender@example.test' end,
    get_queue_id=function() return 'ABCDEF1234' end,
    get_size=function() return 1000 end,
    get_parts=function() return {part} end,
    get_text_parts=function() return {textpart} end,
    get_header_count=function() return 0 end,
    get_header_raw=function(_,name)
      if name:lower()=='content-type' then return ct end
      if name:lower()=='content-transfer-encoding' then return cte end
      if name:lower()=='in-reply-to' and reply == true then return '<previous@example.test>' end
      if name:lower()=='subject' and reply == 'forward' then return 'Fwd: Some earlier message' end
      return nil
    end,
    get_raw_headers=function() return top_headers end,
    get_rawbody=function() return body end,
    get_newlines_type=function() return 'crlf' end,
    set_message=function(_, data)
      result.modified=type(data)=='table' and table.concat(data) or data
      return true
    end,
    set_milter_reply=function(_, val) result.mime_actions=val end,
    set_pre_result=function(_,action,message,module) result.pre_result={action=action,message=message,module=module} end,
  }
  return task,result
end

local function run_and_save(path, ...)
  local t, r = task_case(...)
  status=200
  assert(callback(t))
  assert(r.modified, 'expected rewritten MIME')
  local f=assert(io.open(path,'wb'));f:write(r.modified);f:close()
  return r
end
local dir=assert(arg[1], 'fixture directory required')
local plain=run_and_save(dir..'/flat_plain.eml',
  'text/plain; charset=iso-8859-1', 'base64', 'R3LDvMOfZQ==', 'Grüße und Link', false)
-- Quoted-printable encodes the whitespace before the link as =20.
-- Validate the readable text after MIME decoding in the Python parser test.
assert(plain.modified:find('https://example.test/contact', 1, true),
  'plain signature must retain the link target')
assert(plain.modified:find('multipart/alternative',1,true), 'text/plain conversion changed unexpectedly')

local html=run_and_save(dir..'/flat_html.eml',
  'text/html; charset=iso-8859-1', 'base64\r\n\t', '<encoded HTML>',
  '<html><body>Grüße und Test</body></html>', true, 'X-Custom: folded\r\n\tand intact\r\n')
assert(html.modified:find('X-Custom: folded\r\n\tand intact',1,true),'folded unrelated header lost')
assert(not html.modified:find('base64',1,true),'folded old CTE not removed')

-- Multiple markers: only the bottom one is replaced; all previous ones
-- stay visible even when they name a different signature.
local top_tag = '<---[Sales]--[a1b2]--->'
local bottom_tag = '<---[Support]--[c3d4]--->'
local marker_case = '<html><body>Hello<br>' .. top_tag .. '<br>' .. bottom_tag .. '</body></html>'
local marker_result = run_and_save(dir..'/flat_last_marker.eml',
  'text/html; charset=utf-8', '7bit', marker_case, marker_case, true)
assert(marker_result.modified:find(top_tag, 1, true),
  'earlier marker must remain unchanged in HTML')
assert(not marker_result.modified:find(bottom_tag, 1, true),
  'last marker must be replaced in HTML')
assert(marker_result.modified:find('OpenSigWeave', 1, true),
  'signature missing at last marker position')

local same_case = '<html><body>' .. top_tag .. '<br>' .. top_tag .. '</body></html>'
local same_result = run_and_save(dir..'/flat_repeated_marker.eml',
  'text/html; charset=utf-8', '7bit', same_case, same_case, true)
local _, top_count = same_result.modified:gsub(top_tag, '')
assert(top_count == 1, 'only the final repeated marker may be replaced')

local plain_case = 'Hello\r\n' .. top_tag .. '\r\n' .. bottom_tag
local plain_marker = run_and_save(dir..'/flat_plain_last_marker.eml',
  'text/plain; charset=utf-8', '7bit', plain_case, plain_case, false)
assert(plain_marker.modified:find(top_tag, 1, true),
  'earlier marker must remain in plain text')
assert(not plain_marker.modified:find(bottom_tag, 1, true),
  'final marker must be replaced in both converted alternatives')

local already_signed = '<html><body>Hi <b>OpenSigWeave Footer</b> '
  .. '<a href="https://example.test/contact">Contact</a></body></html>'
local already=run_and_save(dir..'/flat_already_signed.eml',
  'text/html; charset=utf-8', '7bit', already_signed, already_signed, true)
-- The Lua mock QP encoder emits =20 for spaces; Python validates decoded content.
local _, count = already.modified:gsub('OpenSigWeave=20Footer','')
assert(count == 1, 'selected signature must not be inserted twice')

local quoted = '<html><body>New reply<div class="gmail_quote">Old '
  .. response.html .. '</div></body></html>'
local reply=run_and_save(dir..'/flat_reply.eml',
  'text/html; charset=utf-8','7bit', quoted, quoted, true, '', true)
local _, occurrences = reply.modified:gsub('OpenSigWeave=20Footer','')
assert(occurrences == 2, 'quoted previous signature must not suppress new signature')
assert(reply.modified:find('OpenSigWeave=20Footer') < reply.modified:find('gmail_quote'),
  'signature must precede quoted history')

-- SOGo HTML replies place the German attribution inside a div/p *before*
-- the actual quoted block. Inserting directly before blockquote is too late.
-- The quote contains an earlier signature to exercise repeated replies.
local sogo_nested_content = '<html><body><div>test 4</div>'
  .. '<div class="reply-attribution">Am Samstag, November 21, 2026 16:20 CET, schrieb '
  .. '<a href="mailto:admin@dev.veiglbergtech.com">admin@dev.veiglbergtech.com</a>:</div>'
  .. '<blockquote type="cite"><p>hi martin</p><p>test</p>'
  .. '<div>Am Samstag, November 21, 2026 16:20 CET, schrieb Martin:</div>'
  .. '<blockquote type="cite"><p>test</p><div>' .. response.html
  .. '</div></blockquote></blockquote></body></html>'
local sogo=run_and_save(dir..'/flat_sogo_nested_reply.eml',
  'text/html; charset=utf-8', '7bit', sogo_nested_content, sogo_nested_content, true, '', true)
local sogo_new_signature = sogo.modified:find('OpenSigWeave=20Footer', 1, true)
local sogo_attribution = sogo.modified:find('Am=20Samstag', 1, true)
assert(sogo_new_signature and sogo_attribution and sogo_new_signature < sogo_attribution,
  'SOGo signature must precede the first German attribution, not just blockquote')
local _, sogo_count = sogo.modified:gsub('OpenSigWeave=20Footer', '')
assert(sogo_count == 2, 'nested quote must retain its original signature exactly once')

local sogo_paragraph_content = '<html><body><p>New response</p>'
  .. '<p>Am Samstag, November 21, 2026 16:20 CET, schrieb admin@dev.veiglbergtech.com:</p>'
  .. '<blockquote type="cite"><p>Original response</p></blockquote></body></html>'
local sogo_paragraph=run_and_save(dir..'/flat_sogo_paragraph_reply.eml',
  'text/html; charset=utf-8', '7bit', sogo_paragraph_content, sogo_paragraph_content, true, '', true)
assert(sogo_paragraph.modified:find('OpenSigWeave=20Footer', 1, true)
  < sogo_paragraph.modified:find('Am=20Samstag', 1, true),
  'paragraph-wrapped SOGo quote introduction is quoted history too')

-- Exercise the actual multipart/alternative callback path used by SOGo in
-- Mailcow, not just the flat HTML path. Both alternatives must place the
-- newly selected signature before the first German quoted attribution.
local multipart_plain = 'test 4\r\n\r\n'
  .. 'Am Samstag, November 21, 2026 16:20 CET, schrieb admin@dev.veiglbergtech.com:\r\n'
  .. '\r\n> hi martin\r\n> test\r\n> Am Samstag, November 21, 2026 16:20 CET, schrieb Martin:\r\n'
  .. '> > test\r\n> > OpenSigWeave Footer Contact (https://example.test/contact)'
local multipart_html = sogo_nested_content
local boundary = 'sogo-alt'
local multipart_task, multipart_result = task_case(
  'multipart/alternative; boundary="' .. boundary .. '"', '7bit', '', '', false, '', true)

local function mock_mime_part(major, minor, part_headers, content)
  local p = {}
  function p:get_type() return major, minor end
  function p:is_multipart() return major == 'multipart' end
  function p:is_message() return false end
  function p:is_attachment() return false end
  function p:is_text() return major == 'text' end
  function p:get_boundary() return boundary end
  function p:get_enclosing_boundary() return boundary end
  function p:get_raw_headers() return part_headers or '' end
  function p:get_raw_content() return content or '' end
  function p:get_headers()
    if major ~= 'text' then return {} end
    local typ = 'text/' .. minor .. '; charset=utf-8'
    return {
      {name='Content-Type', value=typ, raw='Content-Type: ' .. typ},
      {name='Content-Transfer-Encoding', value='7bit', raw='Content-Transfer-Encoding: 7bit'},
    }
  end
  function p:get_text()
    return {
      is_html=function() return minor == 'html' end,
      get_content=function(_, format)
        assert(format == 'raw_utf')
        return content
      end,
    }
  end
  return p
end

local plain_headers = 'Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 7bit\r\n'
local html_headers = 'Content-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: 7bit\r\n'
local root_part = mock_mime_part('multipart', 'alternative')
local plain_part = mock_mime_part('text', 'plain', plain_headers, multipart_plain)
local html_part = mock_mime_part('text', 'html', html_headers, multipart_html)
multipart_task.get_parts=function() return {root_part, plain_part, html_part} end
multipart_task.get_text_parts=function() return {plain_part:get_text(), html_part:get_text()} end
multipart_task.get_rawbody=function()
  return '--' .. boundary .. '\r\n' .. plain_headers .. '\r\n' .. multipart_plain
    .. '\r\n--' .. boundary .. '\r\n' .. html_headers .. '\r\n' .. multipart_html
    .. '\r\n--' .. boundary .. '--\r\n'
end
status=200
assert(callback(multipart_task))
assert(multipart_result.modified, 'multipart SOGo reply was not rewritten')
local multipart_fixture=assert(io.open(dir..'/multipart_sogo_nested_reply.eml','wb'))
multipart_fixture:write(multipart_result.modified)
multipart_fixture:close()


-- Two tags in both multipart alternatives: preserve the upper tag and
-- replace only the last one with the selected signature in each representation.
local markers_plain = 'Body\r\n' .. top_tag .. '\r\n' .. bottom_tag
local markers_html = '<html><body>Body<br>' .. top_tag .. '<br>' .. bottom_tag .. '</body></html>'
local markers_task, markers_result = task_case(
  'multipart/alternative; boundary="' .. boundary .. '"', '7bit', '', '', false)
local markers_plain_part = mock_mime_part('text', 'plain', plain_headers, markers_plain)
local markers_html_part = mock_mime_part('text', 'html', html_headers, markers_html)
markers_task.get_parts=function() return {root_part, markers_plain_part, markers_html_part} end
markers_task.get_text_parts=function()
  return {markers_plain_part:get_text(), markers_html_part:get_text()}
end
markers_task.get_rawbody=function()
  return '--' .. boundary .. '\r\n' .. plain_headers .. '\r\n' .. markers_plain
    .. '\r\n--' .. boundary .. '\r\n' .. html_headers .. '\r\n' .. markers_html
    .. '\r\n--' .. boundary .. '--\r\n'
end
assert(callback(markers_task))
assert(markers_result.modified, 'multipart message with repeated markers must be rewritten')
assert(markers_result.modified:find(top_tag, 1, true),
  'upper tag must remain in multipart output')
assert(not markers_result.modified:find(bottom_tag, 1, true),
  'last marker must be replaced in both multipart alternatives')
local _, injected_count = markers_result.modified:gsub('OpenSigWeave', '')
assert(injected_count == 2, 'plain and HTML alternatives must each receive a signature')

-- Regression: the real SOGo 5.12.x plain reply is converted to two MIME
-- alternatives. Its current signature must precede the 19:46 attribution in
-- *both* alternatives, even when earlier signatures remain in quote history.
local sogo_real_plain = 'und noch ein test\r\n\r\n'
  .. 'Am Dienstag, November 24, 2026 19:46 CET, schrieb "Martin" <martin@dev.veiglbergtech.com>:\r\n'
  .. '\r\n> and test 56\r\n> Am Dienstag, November 24, 2026 19:45 CET, schrieb "Martin":\r\n'
  .. '> Frohe Weihnachten\r\n> OpenSigWeave Footer'
local converted_sogo = run_and_save(dir..'/flat_plain_sogo_reply.eml',
  'text/plain; charset=utf-8', 'quoted-printable',
  sogo_real_plain, sogo_real_plain, false, '', true)
assert(converted_sogo.modified:find('multipart/alternative', 1, true))

-- Also cover SOGo HTML with attribution following <br> rather than a div/p.
local sogo_br_html = '<html><body><div>und noch ein test</div><br><br>'
  .. 'Am Dienstag, November 24, 2026 19:46 CET, schrieb "Martin" &lt;martin@dev.veiglbergtech.com&gt;:'
  .. '<br><blockquote><p>and test 56</p><p>' .. response.html .. '</p></blockquote></body></html>'
local br_task, br_result = task_case(
  'multipart/alternative; boundary="' .. boundary .. '"', '7bit', '', '', false, '', true)
local br_plain_part = mock_mime_part('text', 'plain', plain_headers, sogo_real_plain)
local br_html_part = mock_mime_part('text', 'html', html_headers, sogo_br_html)
br_task.get_parts=function() return {root_part, br_plain_part, br_html_part} end
br_task.get_text_parts=function() return {br_plain_part:get_text(), br_html_part:get_text()} end
br_task.get_rawbody=function()
  return '--' .. boundary .. '\r\n' .. plain_headers .. '\r\n' .. sogo_real_plain
    .. '\r\n--' .. boundary .. '\r\n' .. html_headers .. '\r\n' .. sogo_br_html
    .. '\r\n--' .. boundary .. '--\r\n'
end
status=200
assert(callback(br_task))
assert(br_result.modified, 'multipart SOGo br reply was not signed')
local br_fixture=assert(io.open(dir..'/multipart_sogo_br_reply.eml', 'wb'))
br_fixture:write(br_result.modified)
br_fixture:close()

-- Full CID adapter callback, not just a direct test of the MIME wrapper:
-- assert both the related MIME envelope and its outgoing Milter Content-Type.
local prior_signature_html = response.html
local digest = '7428e98493880d020400580e2ce7e49cb21b3c55d4dd09d6c401c54e1d7d0817'
local cid_id = 'osw-' .. digest .. '@assets.opensigweave.invalid'
response.html = '<b>OpenSigWeave Footer</b><img src="cid:' .. cid_id .. '">'
response.inline_assets = {{
  filename=digest..'.png', content_id=cid_id,
  content_type='image/png', base64='iVBORw0KGgp0ZXN0',
}}
inline_images_mode = 'cid'
dofile('rspamd/opensigweave.lua')
local cid_task, cid_result = task_case(
  'multipart/alternative; boundary="' .. boundary .. '"', '7bit', '', '', false, '', true)
cid_task.get_parts=br_task.get_parts
cid_task.get_text_parts=br_task.get_text_parts
cid_task.get_rawbody=br_task.get_rawbody
status=200
assert(callback(cid_task))
assert(cid_result.modified and cid_result.modified:find('multipart/related', 1, true),
  'CID mode must wrap SOGo multipart response in related MIME')
assert(cid_result.mime_actions
    and cid_result.mime_actions.add_headers['Content-Type'].value:find('multipart/related', 1, true),
  'Milter must advertise multipart/related instead of stale multipart/alternative')
local cid_fixture=assert(io.open(dir..'/multipart_sogo_cid_reply.eml', 'wb'))
cid_fixture:write(cid_result.modified)
cid_fixture:close()
response.html = prior_signature_html
response.inline_assets = nil
inline_images_mode = 'external'
dofile('rspamd/opensigweave.lua')

local forward_content = '<html><body>Here is the requested message'
  .. '<div class="gmail_quote">----- Original Message -----</div></body></html>'
response.inject_on_replies = false
local forwarded=run_and_save(dir..'/flat_forward.eml',
  'text/html; charset=utf-8','7bit', forward_content, forward_content, true, '', 'forward')
assert(forwarded.modified:find('OpenSigWeave=20Footer') < forwarded.modified:find('gmail_quote'),
  'signature must precede forwarded history')
response.inject_on_replies = true

local errtask, err = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
status=503
assert(callback(errtask))
assert(err.modified==nil, 'API error must not trigger body rewrite')
assert(diagnostic_logs[#diagnostic_logs]:find('status=503 transport_error=false',1,true),
  'HTTP response failures must log the safe HTTP status')
request_error = 'sensitive error details must never be logged'
local network_probe, network_probe_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
status=0
assert(callback(network_probe))
assert(not network_probe_result.modified)
assert(diagnostic_logs[#diagnostic_logs]:find('status=unknown transport_error=true',1,true),
  'transport failures must be distinguishable without logging raw exceptions')
assert(not diagnostic_logs[#diagnostic_logs]:find('sensitive',1,true),
  'transport error details must not leak')
request_error = nil

local signed, signed_res = task_case('multipart/signed; boundary="sig"','7bit','','',true)
signed.get_parts=function() return {{
  get_type=function() return 'multipart','signed' end,
  is_message=function() return false end,
}} end
status=200
assert(callback(signed)==false, 'signed MIME must be skipped before HTTP')
assert(signed_res.modified==nil)
io.stderr:write('Flat encoding, signature idempotency, reply/forward placement and fail-open checks passed\n')

-- Enabled must never cause a permanent rejection on a technical failure.
failure_policy = 'tempfail'
dofile('rspamd/opensigweave.lua')
for _, http_status in ipairs({401, 403, 422, 500, 503}) do
  status = http_status
  local t, r = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
  assert(callback(t))
  assert(r.modified == nil, 'engine failure should not mutate mail')
  assert(r.pre_result and r.pre_result.action == 'soft reject', 'enabled mode must defer')
  assert(r.pre_result.message:find('Error ID:', 1, true))
end
status = 200
response.html = 123
local invalid, invalid_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(invalid))
assert(invalid_result.pre_result and invalid_result.pre_result.action == 'soft reject',
  'malformed API payload must defer')
response.html = '<b>OpenSigWeave Footer</b>'
local mut, mut_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
mut.set_message = function() return false end
assert(callback(mut))
assert(mut_result.pre_result and mut_result.pre_result.action == 'soft reject',
  'failed MIME mutation must defer')
-- Network/TLS errors and failed scheduling must not silently accept unsigned mail.
request_error = 'TLS handshake or connection timeout'
local network, network_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(network))
assert(network_result.pre_result and network_result.pre_result.action == 'soft reject',
  'network/TLS error must defer')
request_error = nil
request_scheduled = false
local scheduler, scheduler_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(scheduler))
assert(scheduler_result.pre_result and scheduler_result.pre_result.action == 'soft reject',
  'failed asynchronous scheduling must defer')
request_scheduled = true

-- Do not create 4xx responses for unauthenticated inbound mail.
local incoming, incoming_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
incoming.get_user = function() return nil end
incoming.get_from = function() error('must not inspect inbound sender') end
assert(callback(incoming) == false)
assert(not incoming_result.pre_result, 'unauthenticated mail must remain unchanged')

-- A Lua exception before HTTP and another inside the asynchronous callback must
-- both be classified as technical failures of authenticated submission.
local sync_crash, sync_crash_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
sync_crash.get_from = function() error('mock prefilter fault') end
assert(callback(sync_crash) == false)
assert(sync_crash_result.pre_result and sync_crash_result.pre_result.action == 'soft reject',
  'unexpected prefilter error must defer')
local previous_response = response
response = setmetatable({}, {__index=function() error('mock async callback fault') end})
local async_crash, async_crash_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(async_crash))
assert(async_crash_result.pre_result and async_crash_result.pre_result.action == 'soft reject',
  'unexpected callback error must defer')
response = previous_response

-- NONE and reply-disabled are intentional policy skips, not technical failures.
response.html = ''
local none, none_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(none) and not none_result.pre_result and not none_result.modified,
  'NONE policy must not tempfail')
response.html = '<b>OpenSigWeave Footer</b>'
response.inject_on_replies = false
local reply_skip, reply_skip_result = task_case('text/html; charset=utf-8','7bit','reply','reply',true,nil,true)
assert(callback(reply_skip) and not reply_skip_result.pre_result and not reply_skip_result.modified,
  'explicit reply exclusion must not tempfail')
response.inject_on_replies = true

local valid, valid_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(valid))
assert(valid_result.modified and not valid_result.pre_result, 'healthy submission must pass')

-- Public modes: disabled and enabled. Keep legacy aliases.
failure_policy = 'disabled'
dofile('rspamd/opensigweave.lua')
status = 503
local disabled_task, disabled_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(disabled_task) and not disabled_result.pre_result, 'disabled must not reject an engine failure')
for _, mode in ipairs({'enabled','standard','tempfail'}) do
  failure_policy = mode
  dofile('rspamd/opensigweave.lua')
  local t, r = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
  assert(callback(t) and r.pre_result and r.pre_result.action == 'soft reject',
    mode .. ' must temporarily reject an engine failure')
end
failure_policy = 'enabled'
status = 200
dofile('rspamd/opensigweave.lua')
local recovered, result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(recovered) and result.modified and not result.pre_result, 'enabled must pass a healthy message')
-- Exercise real adapter HTTP callback failure path with locally verified
-- domain-specific exemptions. It must not depend on OpenSigWeave availability.
local original_open = io.open
policy_file = '/etc/rspamd/local.d/opensigweave-domain-policy.json'
offline_policy_fixture = {
  format='opensigweave-domain-policy-v2',
  generated_at=os.time() - 3 * 86400,
  policy_version='a6d05536fe3c9096fe2f3bd552ceed4f53aca57e3e1666d67b6e4b551f4ac544',
  domains={ ['example.test']=false, ['required.test']=true },
}
io.open = function(path, mode)
  if path == policy_file then
    return {read=function() return 'OSW_OFFLINE_POLICY' end, close=function() end}
  end
  return original_open(path,mode)
end
failure_policy = 'enabled'
status = 503
dofile('rspamd/opensigweave.lua')
local exempt, exempt_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(exempt) and not exempt_result.pre_result and not exempt_result.modified,
  'verified Off domain must pass unchanged on HTTP failure despite global enabled')
local required, required_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
required.get_from = function() return {{addr='someone@REQUIRED.test'}} end
assert(callback(required) and required_result.pre_result
  and required_result.pre_result.action == 'soft reject',
  'required domain must receive temporary rejection on engine HTTP failure')
local unknown, unknown_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
unknown.get_from = function() return {{addr='other@unknown.test'}} end
assert(callback(unknown) and unknown_result.pre_result
  and unknown_result.pre_result.action == 'soft reject',
  'unknown domain must never silently become exempt')
status = 200
response.html = '<b>OpenSigWeave Footer</b>'
local healthy_exempt, healthy_exempt_result = task_case(
  'text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(healthy_exempt) and healthy_exempt_result.modified
  and not healthy_exempt_result.pre_result,
  'Off domain must still receive its normal signature while engine is healthy')
status = 503
-- Verify a 3-day outage does not expire a local v2 exemption.
local persisted, persisted_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(persisted) and not persisted_result.pre_result and not persisted_result.modified,
  'trusted Off must survive 24-hour outage')
-- An API failure must never rewrite even a plain message containing a selector.
local tagged = '<---[Sales]--[a1b2]--->'
local marked, marked_result = task_case('text/html; charset=utf-8','7bit', tagged, tagged, true)
marked.get_header_count = function() return 1 end
marked.modify_header = function() marked_result.header_mutated = true end
assert(callback(marked) and not marked_result.pre_result and not marked_result.modified
  and not marked_result.header_mutated,
  'exempt outage must retain original header, selector and body')
offline_policy_fixture.domains['example.test']=true
local tampered, tampered_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(tampered) and tampered_result.pre_result
  and tampered_result.pre_result.action == 'soft reject',
  'tampering with a valid-looking but mismatched digest fails closed')
offline_policy_fixture.domains['example.test']=false
offline_policy_fixture.policy_version='corrupt'
local corrupted, corrupted_result = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
assert(callback(corrupted) and corrupted_result.pre_result
  and corrupted_result.pre_result.action == 'soft reject',
  'invalid local policy must never be treated as an exemption')
io.open = original_open
io.stderr:write('OpenSigWeave disabled/enabled/domain-specific fixtures passed\n')
