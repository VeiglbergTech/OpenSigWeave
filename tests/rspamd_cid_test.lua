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
local wrong_cid = {
  filename=asset.filename, content_id='osw-'..string.rep('b',64)..'@assets.opensigweave.invalid',
  content_type='image/png', base64=asset.base64,
}
assert(not cid.wrap(task,original,{wrong_cid}), 'content ID must match filename digest')
local wrong_type = {
  filename=asset.filename, content_id=asset.content_id,
  content_type='image/jpeg', base64=asset.base64,
}
assert(not cid.wrap(task,original,{wrong_type}), 'media type must match file extension')
local collision_task = {
  get_newlines_type=function() return 'crlf' end,
  get_parts=function() return {{
    get_headers=function() return {{name='Content-ID',value='<'..asset.content_id..'>'}} end
  }} end,
}
-- Simulate actual Rspamd MIME parts: get_headers() yields opaque
-- rspamd_text, while get_header_raw() exposes a specific field.
local rspamd_task = {
  get_newlines_type=function() return 'crlf' end,
  get_parts=function() return {{
    get_headers=function() error('must not parse opaque Rspamd header buffer') end,
    get_header_raw=function(_, name)
      assert(name == 'Content-ID')
      return nil
    end,
    get_header_count=function(_, name)
      assert(name == 'Content-ID')
      return 0
    end,
  }} end,
}
local realistic, realistic_type = cid.wrap(rspamd_task, original, {asset})
assert(realistic and realistic_type, realistic_type)
local realistic_collision_task = {
  get_newlines_type=function() return 'crlf' end,
  get_parts=function() return {{
    get_header_raw=function() return '<'..asset.content_id..'>' end,
    get_header_count=function() return 1 end,
  }} end,
}
local collision_real, collision_real_reason = cid.wrap(realistic_collision_task, original, {asset})
assert(not collision_real and collision_real_reason == 'existing content ID collision')
local collision, collision_reason=cid.wrap(collision_task,original,{asset})
assert(not collision and collision_reason=='existing content ID collision')

local function write_fixture(path, content)
  local file=assert(io.open(path,'wb'))
  file:write(content)
  file:close()
end

-- HTML-only messages must remain readable with a CID sibling.
local html_only = table.concat({
  'From: alice@example.org',
  'To: bob@example.org',
  'Subject: html-only',
  'MIME-Version: 1.0',
  'Content-Type: text/html; charset=utf-8',
  'Content-Transfer-Encoding: 8bit',
  '',
  '<p>Hello <img src="cid:'..asset.content_id..'"></p>',
  '',
}, '\r\n')
local html_result, html_type = cid.wrap(task, html_only, {asset})
assert(html_result, html_type)
write_fixture(arg[1]..'.html', html_result)

-- Keep pre-existing inline images and unrelated binary attachments intact,
-- even when the root is a nested multipart/mixed/related structure.
local mixed_original = table.concat({
  'From: alice@example.org',
  'To: bob@example.org',
  'Subject: mixed and related',
  'MIME-Version: 1.0',
  'Content-Type: multipart/mixed; boundary="mixed"',
  '',
  '--mixed',
  'Content-Type: multipart/related; boundary="original-related"',
  '',
  '--original-related',
  'Content-Type: text/html; charset=utf-8',
  '',
  '<p>Existing <img src="cid:brand@example.org"> new <img src="cid:'..asset.content_id..'"></p>',
  '--original-related',
  'Content-Type: image/png',
  'Content-Disposition: inline; filename="original.png"',
  'Content-ID: <brand@example.org>',
  'Content-Transfer-Encoding: base64',
  '',
  'b2xkLXBpY3R1cmU=',
  '--original-related--',
  '--mixed',
  'Content-Type: application/octet-stream',
  'Content-Disposition: attachment; filename="report.dat"',
  'Content-Transfer-Encoding: base64',
  '',
  'AAECAwQF',
  '--mixed--',
  '',
}, '\r\n')
local mixed_task = {
  get_newlines_type=function() return 'crlf' end,
  get_parts=function() return {{
    get_headers=function() return {{name='Content-ID', value='<brand@example.org>'}} end,
  }, {}, {}, {}} end,
}
-- >76 base64 characters exercise correct MIME line folding.
local long_asset = {
  filename=asset.filename, content_id=asset.content_id,
  content_type=asset.content_type,
  base64='iVBORw0KGgp0ZXN0'..string.rep('QUJD',40),
}
local mixed_result, mixed_type = cid.wrap(mixed_task, mixed_original, {long_asset})
assert(mixed_result, mixed_type)
write_fixture(arg[1]..'.mixed', mixed_result)
