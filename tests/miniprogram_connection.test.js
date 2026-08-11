const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const connection = require('../miniprogram/utils/connection')
const {
  createRequestId,
  createPendingRequestTracker
} = require('../miniprogram/utils/request-id')
const { retryPendingWrite } = require('../miniprogram/utils/pending-write')

test('normalizes a private LAN address', () => {
  assert.equal(
    connection.normalizeBaseUrl(' http://192.168.31.68:8000/ '),
    'http://192.168.31.68:8000'
  )
})

test('rejects public, https, and path-bearing addresses', () => {
  assert.throws(
    () => connection.normalizeBaseUrl('https://192.168.1.2:8000'),
    /HTTP/
  )
  assert.throws(
    () => connection.normalizeBaseUrl('http://8.8.8.8:8000'),
    /局域网/
  )
  assert.throws(
    () => connection.normalizeBaseUrl('http://192.168.1.2:8000/admin'),
    /路径/
  )
})

test('parses only version 1 connection QR payloads', () => {
  assert.deepEqual(
    connection.parseConnectionPayload(
      '{"version":1,"base_url":"http://10.0.0.8:8000"}'
    ),
    { version: 1, base_url: 'http://10.0.0.8:8000' }
  )
  assert.throws(
    () =>
      connection.parseConnectionPayload(
        '{"version":2,"base_url":"http://10.0.0.8:8000"}'
      ),
    /版本/
  )
})

test('release uses explicit personal HTTPS endpoint and cannot edit connection', () => {
  assert.equal(
    connection.baseUrlForEnvironment('release', {
      releaseBaseUrl: 'https://personal-inventory.example.test',
      wxApi: { getStorageSync: () => 'http://192.168.1.9:8000' }
    }),
    'https://personal-inventory.example.test'
  )
  assert.equal(connection.canEditConnection('release'), false)
  assert.equal(connection.canEditConnection('develop'), true)
})

test('release ignores saved LAN and rejects absent or IP endpoints', () => {
  const wxApi = { getStorageSync: () => 'http://192.168.1.9:8000' }

  assert.throws(
    () => connection.baseUrlForEnvironment('release', { releaseBaseUrl: '', wxApi }),
    /正式环境/
  )
  assert.throws(
    () => connection.baseUrlForEnvironment('release', {
      releaseBaseUrl: 'https://203.0.113.7',
      wxApi
    }),
    /域名/
  )
})

test('app selects endpoint from mini-program environment and release config', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '../miniprogram/app.js'),
    'utf8'
  )

  assert.match(source, /getAccountInfoSync/)
  assert.match(source, /release-config/)
  assert.match(source, /baseUrlForEnvironment/)
  assert.match(source, /canEditConnection/)
})

test('release connection page hides LAN scan and manual controls', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '../miniprogram/pages/connection/index.js'),
    'utf8'
  )
  const view = fs.readFileSync(
    path.join(__dirname, '../miniprogram/pages/connection/index.wxml'),
    'utf8'
  )

  assert.match(source, /canEdit/)
  assert.match(source, /globalData\.baseUrl/)
  assert.match(view, /wx:if="\{\{canEdit\}\}"/)
})

test('request id is stable under injected clock and random source', () => {
  assert.equal(
    createRequestId(() => 1722768000000, () => 0.25),
    'mobile-1722768000000-40000000'
  )
})

test('pending write reuses its request id after an uncertain network failure', () => {
  const saved = new Map()
  const storage = {
    getStorageSync: (key) => saved.get(key),
    setStorageSync: (key, value) => saved.set(key, value),
    removeStorageSync: (key) => saved.delete(key)
  }
  let sequence = 0
  const createId = () => `request-${++sequence}`

  const firstPage = createPendingRequestTracker(
    'product-inbound',
    storage,
    createId
  )
  const firstAttempt = firstPage.withRequestId({ drawing_id: 9, quantity: 3 })

  const reopenedPage = createPendingRequestTracker(
    'product-inbound',
    storage,
    createId
  )
  const retry = reopenedPage.withRequestId({ drawing_id: 9, quantity: 3 })

  assert.equal(firstAttempt.client_request_id, 'request-1')
  assert.equal(retry.client_request_id, 'request-1')
})

test('changed write payload is blocked until the pending request is replayed', () => {
  const saved = new Map()
  const storage = {
    getStorageSync: (key) => saved.get(key),
    setStorageSync: (key, value) => saved.set(key, value),
    removeStorageSync: (key) => saved.delete(key)
  }
  let sequence = 0
  const tracker = createPendingRequestTracker(
    'scrap-outbound',
    storage,
    () => `request-${++sequence}`
  )

  const first = tracker.withRequestId({ scrap_group_key: 'A', quantity: 1 })
  assert.throws(
    () => tracker.withRequestId({ scrap_group_key: 'A', quantity: 2 }),
    (error) => error.code === 'PENDING_REQUEST_UNRESOLVED'
  )
  const replay = tracker.retryPending()
  tracker.complete()
  const afterSuccess = tracker.withRequestId({
    scrap_group_key: 'A',
    quantity: 2
  })

  assert.equal(first.client_request_id, 'request-1')
  assert.deepEqual(replay, first)
  assert.equal(afterSuccess.client_request_id, 'request-2')
})

test('changed write can only continue by explicitly replaying the pending payload', async () => {
  const saved = new Map()
  const storage = {
    getStorageSync: (key) => saved.get(key),
    setStorageSync: (key, value) => saved.set(key, value),
    removeStorageSync: (key) => saved.delete(key)
  }
  const tracker = createPendingRequestTracker(
    'product-outbound',
    storage,
    () => 'request-pending'
  )
  tracker.withRequestId({ drawing_id: 7, quantity: 3 })
  const modal = (options) => options.success({ confirm: true })

  const replay = await retryPendingWrite(
    tracker,
    { drawing_id: 7, quantity: 4 },
    '产品出库',
    modal
  )

  assert.deepEqual(replay, {
    drawing_id: 7,
    quantity: 3,
    client_request_id: 'request-pending'
  })
})
