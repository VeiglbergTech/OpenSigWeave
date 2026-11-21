-- Verify CID images wrap an existing MIME tree without changing its leaves.
package.path = 'rspamd/?.lua;' .. package.path
package.preload['rspamd_util'] = function()
  return {random_hex=function() return '0011223344556677' end}
end
local cid = require('opensigweave_cid')
local task = {
  get_newlines_type=function() return 'crlf' end,
  get_parts=function() return {{}, {}, {}} end,
}
local original =
  'From: alice@example.org\r\nTo: bob@example.org\r\n' ..
  'Subject: nested CID\r\nMIME-Version: 1.0\r\nX-Trace: intact\r\n' ..
  'Content-Type: multipart/alternative; boundary="alt"\r\n\r\n' ..
  '--alt\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n' ..
  '--alt\r\nContent-Type: text/html; charset=utf-8\r\n\r\n' ..
  '<p>Hello <img src="cid:osw-' .. string.rep('a',64) ..
  '@assets.opensigweave.invalid"></p>\r\n--alt--\r\n'
local asset = {
  filename=string.rep('a',64)..'.png',
  content_id='osw-'..string.rep('a',64)..'@assets.opensigweave.invalid',
  content_type='image/png',
  base64='iVBORw0KGgp0ZXN0',
}
local result, kind = cid.wrap(task,original,{asset})
assert(result, kind)
assert(kind:find('multipart/related',1,true))
assert(result:find('X-Trace: intact',1,true))
assert(result:find('--alt--\r\n',1,true))
assert(result:find('Content-Disposition: inline',1,true))
local f=assert(io.open(assert(arg[1]),'wb'))
f:write(result)
f:close()
local duplicate, reason=cid.wrap(task,original,{asset,asset})
assert(not duplicate and reason=='duplicate content ID')
local bad = {filename='../bad',content_id=asset.content_id,content_type='image/png',base64='AAAA'}
assert(not cid.wrap(task,original,{bad}), 'unsafe file reference should not be accepted')
