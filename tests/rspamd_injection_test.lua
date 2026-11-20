-- Execute the Rspamd plugin with mocked network/parser but genuine body logic.
package.path = 'rspamd/?.lua;' .. package.path
local callback, status = nil, 200
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
  return { request=function(opts) opts.callback(nil, status, '{}', {}); return true end }
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
  } end,
  register_symbol=function(_,sym) callback=sym.callback end,
}
dofile('rspamd/opensigweave.lua')
assert(type(callback)=='function')

local function task_case(ct, cte, body, decoded, is_html, other_headers, reply)
  local top_headers = 'From: sender@example.test\r\nTo: receiver@example.test\r\nMIME-Version: 1.0\r\n'
    .. 'Subject: Grüße und Prüfung\r\n' .. (other_headers or '')
    .. 'Content-Type: ' .. ct .. '\r\nContent-Transfer-Encoding: ' .. cte .. '\r\n'
  local result = {modified=nil, mime_actions=nil}
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
assert(plain.modified:find('Contact %(https://example.test/contact%)'),
  'plain signature must retain links')
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
local _, count = already.modified:gsub('OpenSigWeave Footer','')
assert(count == 1, 'selected signature must not be inserted twice')

local quoted = '<html><body>New reply<div class="gmail_quote">Old '
  .. response.html .. '</div></body></html>'
local reply=run_and_save(dir..'/flat_reply.eml',
  'text/html; charset=utf-8','7bit', quoted, quoted, true, '', true)
local _, occurrences = reply.modified:gsub('OpenSigWeave Footer','')
assert(occurrences == 2, 'quoted previous signature must not suppress new signature')
assert(reply.modified:find('OpenSigWeave Footer') < reply.modified:find('gmail_quote'),
  'signature must precede quoted history')

local forward_content = '<html><body>Here is the requested message'
  .. '<div class="gmail_quote">----- Original Message -----</div></body></html>'
response.inject_on_replies = false
local forwarded=run_and_save(dir..'/flat_forward.eml',
  'text/html; charset=utf-8','7bit', forward_content, forward_content, true, '', 'forward')
assert(forwarded.modified:find('OpenSigWeave Footer') < forwarded.modified:find('gmail_quote'),
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
