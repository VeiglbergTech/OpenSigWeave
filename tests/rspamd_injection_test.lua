-- Execute the Rspamd plugin with mocked network/parser but genuine body logic.
package.path = 'rspamd/?.lua;' .. package.path
local callback, status = nil, 200
local failure_policy = 'fail_open'
local request_error, request_scheduled = nil, true
local response = {
  html = '<b>OpenSigWeave Footer</b> <a href="https://example.test/contact">Contact</a>',
  inject_on_replies = true,
  trim_whitespace = true,
  strip_device_signatures = false,
}
package.preload['rspamd_logger'] = function()
  return {errx=function() end, warnx=function() end, infox=function() end}
end
package.preload['rspamd_http'] = function()
  return { request=function(opts)
    if not request_scheduled then return false end
    opts.callback(request_error, status, '{}', {})
    return true
  end }
end
package.preload['ucl'] = function()
  return { parser=function()
    return {
      parse_string=function() return true end,
      get_object=function() return response end,
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

local signed, signed_res = task_case('multipart/signed; boundary="sig"','7bit','','',true)
signed.get_parts=function() return {{
  get_type=function() return 'multipart','signed' end,
  is_message=function() return false end,
}} end
status=200
assert(callback(signed)==false, 'signed MIME must be skipped before HTTP')
assert(signed_res.modified==nil)
io.stderr:write('Flat encoding, signature idempotency, reply/forward placement and fail-open checks passed\n')

-- The strict policy must never return a permanent rejection on technical failure.
failure_policy = 'tempfail'
dofile('rspamd/opensigweave.lua')
for _, http_status in ipairs({401, 422, 503}) do
  status = http_status
  local t, r = task_case('text/html; charset=utf-8','7bit','hello','hello',true)
  assert(callback(t))
  assert(r.modified == nil, 'engine failure should not mutate mail')
  assert(r.pre_result and r.pre_result.action == 'soft reject', 'strict mode must defer')
  assert(r.pre_result.message:find('Fehler-ID:', 1, true))
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
