package.path = 'rspamd/?.lua;' .. package.path
package.preload['rspamd_util'] = function()
  return { encode_qp = function(v) return v end }
end
local mime = require 'opensigweave_mime'
local function parse_headers(raw)
  local out = {}
  local current = nil
  local normalized = tostring(raw or ''):gsub('\r\n', '\n'):gsub('\r', '\n')
  for line in (normalized .. '\n'):gmatch('(.-)\n') do
    if line == '' then break end
    if line:match('^[ \t]') and current then
      current.raw = current.raw .. '\r\n' .. line
    else
      local name, value = line:match('^([^:]+):(.*)$')
      if name then
        local separator = value:match('^(%s*)') or ''
        local raw_value = value:sub(#separator + 1)
        current = {
          name = name,
          raw = raw_value,
          value = raw_value,
          separator = separator,
        }
        out[#out + 1] = current
      end
    end
  end
  return out
end

local function part(major, minor, boundary, headers, content, flags)
  flags = flags or {}
  local o = {}
  function o:get_type() return major, minor end
  function o:get_boundary() return boundary end
  function o:get_raw_headers() return headers or '' end
  function o:get_headers() return flags.parsed_headers or parse_headers(headers) end
  function o:get_raw_content() return content or '' end
  function o:is_multipart() return major == 'multipart' end
  function o:is_message() return major == 'message' end
  function o:is_attachment() return flags.attachment or false end
  function o:is_text() return major == 'text' end
  function o:get_text() return { get_content = function(self, form) return flags.decoded or content end } end
  return o
end
local h = 'From: sender@example.com\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="mix"\r\n'
local root = part('multipart','mixed','mix')
local alt = part('multipart','alternative','alt','Content-Type: multipart/alternative; boundary="alt"\r\n')
local plain = part('text','plain','alt','Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 7bit\r\n','Hi\r\nOn Monday wrote:\r\n> Old')
local html = part('text','html','alt','Content-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: 7bit\r\nContent-ID: <body-id>\r\nX_Custom.Header: preserved\r\n','<html><body>Hi<div class="gmail_quote">Old</div></body></html>')
local attach = part('application','octet-stream','mix','Content-Type: application/octet-stream\r\nContent-Transfer-Encoding: base64\r\nContent-Disposition: attachment; filename="test.bin"\r\n','AAEC/w==\r\n',{attachment=true})
local task = {}
function task:get_parts() return {root,alt,plain,html,attach} end
function task:get_size() return 1400 end
function task:get_raw_headers() return h end
function task:get_newlines_type() return 'crlf' end
local chunks, count = mime.rewrite(task, function(s,is_html)
  if is_html then return s:gsub('<div class="gmail_quote">','<p>SIGNATURE</p><div class="gmail_quote">', 1) end
  return s:gsub('On Monday wrote:', 'SIGNATURE\r\nOn Monday wrote:',1)
end)
assert(chunks, count)
assert(count == 2)
local message = table.concat(chunks)
assert(message:find('AAEC/w==',1,true), 'attachment missing')
assert(message:find('Content-ID: <body-id>',1,true), 'Content-ID missing')
assert(message:find('X_Custom.Header: preserved',1,true), 'nonstandard parsed MIME header missing')
assert(message:find('--alt--',1,true) and message:find('--mix--',1,true), 'multipart closures missing')
local signed = part('multipart','signed','signed')
function task:get_parts() return {signed, plain} end
assert(mime.rewrite(task, function(s) return s .. 'INVALID' end) == nil, 'must skip signed MIME')
local broken = part('multipart','mixed',nil)
function task:get_parts() return {broken, plain} end
assert(mime.rewrite(task, function(s) return s .. 'INVALID' end) == nil, 'must skip invalid boundaries')
local embedded = part('message','rfc822','mix')
function task:get_parts() return {root, embedded} end
assert(mime.rewrite(task, function(s) return s .. 'INVALID' end) == nil, 'must skip embedded messages')
io.write(message)
