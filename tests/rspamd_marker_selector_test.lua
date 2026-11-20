package.path = 'rspamd/?.lua;' .. package.path

local captured_request = nil
local registered_callback = nil

package.preload['rspamd_logger'] = function()
  return { errx=function() end, warnx=function() end, infox=function() end }
end
package.preload['rspamd_http'] = function()
  return {
    request=function(opts)
      captured_request = opts
      return true
    end
  }
end
package.preload['ucl'] = function()
  return { parser=function() return {} end }
end
package.preload['rspamd_util'] = function()
  return {
    encode_qp=function(v) return v end,
    decode_qp=function(v) return v end,
    decode_base64=function(v) return v end,
    random_hex=function() return '0123456789ab' end,
  }
end
package.preload['opensigweave_mime'] = function()
  return { rewrite=function() return nil, 'not-called' end }
end

rspamd_config = {
  get_all_opt=function()
    return {
      api_url='https://signature.example.test/api/v1/engine/signatures/',
      engine_api_key=string.rep('x', 40),
      timeout=5,
      max_response_size=65536,
    }
  end,
  register_symbol=function(_, spec)
    registered_callback = spec.callback
  end,
}

dofile('rspamd/opensigweave.lua')
assert(type(registered_callback) == 'function', 'plugin callback not registered')

local function text_part(content, html)
  return {
    get_content=function() return content end,
    is_html=function() return html end,
  }
end

local function run(parts)
  captured_request = nil
  local task = {
    get_from=function() return {{addr='martin@example.test'}} end,
    get_user=function() return 'martin@example.test' end,
    get_header_raw=function() return nil end,
    get_header_count=function() return 0 end,
    get_text_parts=function() return parts end,
  }
  assert(registered_callback(task) == true)
  assert(captured_request, 'HTTP request not scheduled')
  return captured_request.headers
end

-- The same visible marker is expected in both alternatives of an HTML mail.
local marker = '<---[Professional]--[a1b2]--->'
local headers = run({
  text_part('Hello\r\n' .. marker, false),
  text_part('<html><body>Hello<br>' .. marker .. '</body></html>', true),
})
assert(headers['X-OpenSigWeave-Signature'] == 'Professional',
  'identical marker across MIME alternatives must remain valid')
assert(headers['X-OpenSigWeave-Signature-Code'] == 'a1b2')

-- Conflicting selectors across alternatives must remain fail-closed.
headers = run({
  text_part('Hello\r\n<---[Professional]--[a1b2]--->', false),
  text_part('<html><body><---[Internal]--[c3d4]---></body></html>', true),
})
assert(headers['X-OpenSigWeave-Signature'] == '')
assert(headers['X-OpenSigWeave-Signature-Code'] == '')

-- Two marker occurrences inside one representation are still ambiguous.
headers = run({
  text_part(marker .. '\r\n' .. marker, false),
})
assert(headers['X-OpenSigWeave-Signature'] == '')
assert(headers['X-OpenSigWeave-Signature-Code'] == '')

io.write('rspamd marker selector tests passed\n')
