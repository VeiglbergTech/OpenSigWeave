-- Exercise MIME structure against the real Python email parser in CI.
package.path = 'rspamd/?.lua;' .. package.path
package.preload['rspamd_util'] = function()
  return {
    encode_qp=function(input)
      -- Small deterministic fixture encoder; production uses Rspamd's encoder.
      return (tostring(input):gsub('[^%w%p \r\n]', function(c)
        return string.format('=%02X', c:byte())
      end):gsub('=', '=3D'))
    end
  }
end
-- Override the fixture encoder with a standards-compliant byte encoder.
package.loaded['rspamd_util'] = {
  encode_qp=function(input)
    return (tostring(input):gsub('[^\r\n]', function(c)
      local byte = c:byte()
      if (byte >= 33 and byte <= 60) or (byte >= 62 and byte <= 126) then return c end
      return string.format('=%02X', byte)
    end))
  end
}
local mime = require('opensigweave_mime')
local function headers(raw)
  local out = {}
  local current
  for line in (raw .. '\n'):gmatch('(.-)\n') do
    line = line:gsub('\r$', '')
    if line == '' then break end
    if line:match('^[ \t]') and current then
      current.raw = current.raw .. '\r\n' .. line
    else
      local name, value = line:match('^([^:]+):%s*(.*)$')
      assert(name)
      current = {name=name, value=value, raw=line}
      out[#out+1] = current
    end
  end
  return out
end
local function part(major, minor, boundary, enclosed, raw_headers, raw, decoded, attachment)
  local o = {}
  function o:get_type() return major, minor end
  function o:get_boundary() return boundary end
  function o:get_enclosing_boundary() return enclosed end
  function o:get_raw_headers() return raw_headers or '' end
  function o:get_headers() return headers(raw_headers or '') end
  function o:get_raw_content() return raw or '' end
  function o:is_multipart() return major == 'multipart' end
  function o:is_message() return major == 'message' end
  function o:is_attachment() return attachment or false end
  function o:is_text() return major == 'text' end
  function o:get_text()
    if decoded == false then return nil end
    return {get_content=function() return decoded or raw end}
  end
  return o
end
local root = part('multipart','mixed','outer',nil)
local alternative1 = part('multipart','alternative','a1','outer','Content-Type: multipart/alternative; boundary="a1"\r\n')
local plain1 = part('text','plain','a1',nil,'Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n','SGFsbG8gV2VsdCE=','Hallo München 🌍')
local related = part('multipart','related','rel','a1','Content-Type: multipart/related; boundary="rel"\r\n')
local html1 = part('text','html','rel',nil,'Content-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: quoted-printable\r\nContent-ID: <html-part>\r\nX-Body-Trace: 123\r\n','<b>bad raw</b>','<html><body>Grüße <img src="cid:logo1"></body></html>')
local inline = part('image','png','rel',nil,'Content-Type: image/png\r\nContent-Transfer-Encoding: base64\r\nContent-ID: <logo1>\r\nContent-Disposition: inline; filename="logo.png"\r\n','AAEC/w==',nil,false)
local alternative2 = part('multipart','alternative','a2','outer','Content-Type: multipart/alternative; boundary="a2"\r\n')
local plain2 = part('text','plain','a2',nil,'Content-Type: text/plain; charset=utf-8\r\n','New response\r\nOn Wednesday wrote:\r\n> Original',nil,false)
local html2 = part('text','html','a2',nil,'Content-Type: text/html; charset=utf-8\r\n','<html><body>New response<div class="gmail_quote">Original</div></body></html>',nil,false)
local attachment = part('application','octet-stream','outer',nil,'Content-Type: application/octet-stream\r\nContent-Transfer-Encoding: base64\r\nContent-Disposition: attachment; filename="document.bin"\r\nX-File-Trace: intact\r\n','AQIDBA==',nil,true)
local all = {root,alternative1,plain1,related,html1,inline,alternative2,plain2,html2,attachment}
local h = 'From: sender@example.com\r\nTo: recipient@example.com\r\nSubject: Long header\r\n\t' .. string.rep('X', 1500) .. '\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="outer"\r\nX-Trace: value\r\n'
local task = {}
function task:get_parts() return all end
function task:get_size() return 4096 end
function task:get_raw_headers() return h end
function task:get_newlines_type() return 'crlf' end
local chunks, count = mime.rewrite(task, function(s, is_html)
  if s:find('On Wednesday wrote:',1,true) then
    return s:gsub('On Wednesday wrote:', 'OSW_INSERT\r\nOn Wednesday wrote:', 1)
  elseif s:find('gmail_quote',1,true) then
    return s:gsub('<div class="gmail_quote">', '<p>OSW_INSERT</p><div class="gmail_quote">', 1)
  end
  return s .. (is_html and '<p>OSW_INSERT</p>' or '\r\nOSW_INSERT')
end)
assert(chunks, count)
assert(count == 4, 'four text alternatives must be edited')
local output = table.concat(chunks)
assert(output:find('X-Trace: value',1,true), 'top-level header lost')
assert(output:find('X-Body-Trace: 123',1,true), 'nested header lost')
assert(output:find('X-File-Trace: intact',1,true), 'attachment header lost')
assert(output:find('AAEC/w==\r\n--rel--',1,true), 'inline image/boundary separator broken')
assert(output:find('AQIDBA==\r\n--outer--',1,true), 'attachment/boundary separator broken')
assert(output:find('--rel--\r\n--a1--\r\n--outer\r\n',1,true), 'nested sibling boundaries malformed')
local outpath = assert(arg[1], 'output file path required')
local f = assert(io.open(outpath, 'wb'))
f:write(output)
f:close()

-- Message must be skipped atomically for protected/malformed/oversized structures.
local before = all
all = {root, part('multipart','signed','sig','outer'), plain1}
assert(mime.rewrite(task, function(s) return s .. 'DANGER' end) == nil)
all = {root, part('message','rfc822','outer',nil,'','','',false)}
assert(mime.rewrite(task, function(s) return s .. 'DANGER' end) == nil)
all = {root, part('multipart','related','bad\r\nboundary','outer')}
assert(mime.rewrite(task, function(s) return s .. 'DANGER' end) == nil)
all = {root, part('text','plain','outer',nil,'Content-Type: text/plain\r\n','',false)}
assert(mime.rewrite(task, function(s) return s .. 'DANGER' end) == nil)
all = before
function task:get_size() return 21*1024*1024 end
assert(mime.rewrite(task, function(s) return s .. 'DANGER' end) == nil)
function task:get_size() return 4096 end
local oversized = {root}
for i=1,257 do oversized[#oversized+1] = plain2 end
all = oversized
assert(mime.rewrite(task, function(s) return s .. 'DANGER' end) == nil)
io.stderr:write('Rspamd matrix fixture generated and fail-closed guards passed\n')
