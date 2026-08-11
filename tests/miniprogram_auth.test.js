const test = require('node:test')
const assert = require('node:assert/strict')

const auth = require('../miniprogram/utils/auth')

test('authorizationHeader returns Bearer token from storage', () => {
  const wx = { getStorageSync: (key) => key === 'tns_auth_session' ? 'session-123' : '' }
  assert.deepEqual(auth.authorizationHeader(wx), { Authorization: 'Bearer session-123' })
})

test('unauthorized response clears session and opens login page', () => {
  const calls = []
  const wx = {
    removeStorageSync: (key) => calls.push(['remove', key]),
    reLaunch: (options) => calls.push(['reLaunch', options.url])
  }

  auth.handleUnauthorized(wx)

  assert.deepEqual(calls, [
    ['remove', 'tns_auth_session'],
    ['reLaunch', '/pages/auth/login']
  ])
})

test('activation sends wx login code with username and activation code', async () => {
  const saved = []
  const wx = {
    login: (options) => options.success({ code: 'wx-code-1' }),
    setStorageSync: (key, value) => saved.push([key, value])
  }
  let sent
  const request = async (path, options) => {
    sent = [path, options]
    return { token: 'new-session', account: { display_name: '张三', role: 'employee' } }
  }

  const result = await auth.activate(wx, request, 'TNS008', '12345678')

  assert.deepEqual(sent, [
    '/api/auth/wechat/activate',
    {
      method: 'POST',
      data: { username: 'TNS008', activation_code: '12345678', wx_code: 'wx-code-1' }
    }
  ])
  assert.deepEqual(saved, [['tns_auth_session', 'new-session']])
  assert.equal(result.account.display_name, '张三')
})
