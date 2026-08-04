const test = require('node:test')
const assert = require('node:assert/strict')

const connection = require('../miniprogram/utils/connection')
const { createRequestId } = require('../miniprogram/utils/request-id')

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

test('request id is stable under injected clock and random source', () => {
  assert.equal(
    createRequestId(() => 1722768000000, () => 0.25),
    'mobile-1722768000000-40000000'
  )
})
