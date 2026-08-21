const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.join(__dirname, '..', 'miniprogram', 'pages')
const WRITE_PAGES = [
  'inventory/inbound',
  'inventory/outbound',
  'inventory/transactions',
  'scraps/pending',
  'scraps/outbound',
  'scraps/transactions',
  'raw-plates/inbound',
  'raw-plates/outbound',
  'raw-plates/detail',
  'raw-plates/transactions',
  'paper/inbound',
  'paper/outbound',
  'paper/transactions'
]

test('inventory write pages never expose an editable operator field', () => {
  for (const page of WRITE_PAGES) {
    const view = fs.readFileSync(path.join(ROOT, `${page}.wxml`), 'utf8')
    assert.doesNotMatch(view, /data-field=["']operator_name["']/, page)
    assert.doesNotMatch(view, /name=["']operator_name["']/, page)
  }
})

test('write-page source never creates or submits operator_name', () => {
  for (const page of WRITE_PAGES) {
    const source = fs.readFileSync(path.join(ROOT, `${page}.js`), 'utf8')
    assert.doesNotMatch(source, /operator_name\s*:/, page)
    assert.doesNotMatch(source, /form\.operator_name/, page)
  }
})

test('read-only operator name comes from safe account storage', () => {
  const operator = require('../miniprogram/utils/operator')
  const wx = {
    getStorageSync: (key) => key === 'tns_auth_account'
      ? { username: 'tns008', display_name: '张三', role: 'employee' }
      : ''
  }
  assert.equal(operator.currentOperatorName(wx), '张三')
})
