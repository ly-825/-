const test = require('node:test')
const assert = require('node:assert/strict')

const pcLogin = require('../miniprogram/utils/pc-login')

test('valid QR payload returns only request token', () => {
  assert.equal(
    pcLogin.parseQrPayload('tns-inventory-login:v1:abc_DEF-123'),
    'abc_DEF-123'
  )
})

test('URLs malformed versions whitespace and extra separators are rejected', () => {
  for (const value of [
    'https://evil.example/login',
    'tns-inventory-login:v2:abc',
    'tns-inventory-login:v1:',
    'tns-inventory-login:v1:abc def',
    'tns-inventory-login:v1:abc:extra'
  ]) {
    assert.throws(() => pcLogin.parseQrPayload(value))
  }
})

test('scan validates payload before authenticated summary request', async () => {
  const calls = []
  const wx = {
    scanCode: (options) => options.success({ result: 'tns-inventory-login:v1:token_123' })
  }
  const request = async (path, options) => {
    calls.push([path, options])
    return { status: 'scanned', device_summary: 'Chrome' }
  }
  const result = await pcLogin.scanPcLogin(wx, request)
  assert.equal(result.requestToken, 'token_123')
  assert.deepEqual(calls, [[
    '/api/auth/pc-login/scan',
    { method: 'POST', data: { request_token: 'token_123' } }
  ]])
})

test('scan failure makes no decision request', async () => {
  let requestCount = 0
  const wx = { scanCode: (options) => options.success({ result: 'https://evil.example' }) }
  await assert.rejects(() => pcLogin.scanPcLogin(wx, async () => { requestCount += 1 }))
  assert.equal(requestCount, 0)
})

test('approve and deny require an explicit boolean decision', async () => {
  const calls = []
  const request = async (path, options) => { calls.push([path, options]); return { status: options.data.approved ? 'approved' : 'denied' } }
  await pcLogin.decidePcLogin(request, 'token-1', true)
  await pcLogin.decidePcLogin(request, 'token-2', false)
  assert.deepEqual(calls.map((call) => call[1].data), [
    { request_token: 'token-1', approved: true },
    { request_token: 'token-2', approved: false }
  ])
})
