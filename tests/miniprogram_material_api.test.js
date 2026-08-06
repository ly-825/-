const test = require('node:test')
const assert = require('node:assert/strict')

let lastRequest = null
global.getApp = () => ({ globalData: { baseUrl: 'http://factory' } })
global.wx = {
  request(options) {
    lastRequest = options
    options.success({ statusCode: 200, data: { ok: true } })
  }
}

const api = require('../miniprogram/utils/api')

test('plan lookup uses mobile plan endpoints', async () => {
  await api.planDrawings({ q: 'TNX' })
  assert.equal(lastRequest.url, 'http://factory/api/mobile/plans/drawings')
  assert.deepEqual(lastRequest.data, { q: 'TNX' })
  await api.planMatch({ drawing_id: 3, quantity: 10 })
  assert.equal(lastRequest.url, 'http://factory/api/mobile/plans/match')
})

test('raw plate writes use tracked mobile endpoints', async () => {
  await api.rawPlateInbound({ client_request_id: 'raw-1', specification_id: 3, total_weight_ton: 1 })
  assert.equal(lastRequest.url, 'http://factory/api/mobile/raw-plates/inbound')
  assert.equal(lastRequest.method, 'POST')
  assert.throws(() => api.rawPlateOutbound({ quantity: 1 }), /重试编号/)
})

test('paper specification update and outbound preserve methods', async () => {
  await api.updatePaperSpecification(8, { client_request_id: 'paper-spec-1', paper_type: 'roll' })
  assert.equal(lastRequest.url, 'http://factory/api/mobile/paper-specifications/8')
  assert.equal(lastRequest.method, 'PUT')
  await api.paperOutbound({ client_request_id: 'paper-out-1', specification_id: 8, quantity: 2 })
  assert.equal(lastRequest.url, 'http://factory/api/mobile/paper-materials/outbound')
  assert.equal(lastRequest.method, 'POST')
})
