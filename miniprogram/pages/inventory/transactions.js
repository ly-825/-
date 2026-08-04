const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const requestTrackers = new Map()

function trackerFor(transactionId) {
  if (!requestTrackers.has(transactionId)) {
    requestTrackers.set(
      transactionId,
      createPendingRequestTracker(`product-reverse-${transactionId}`)
    )
  }
  return requestTrackers.get(transactionId)
}

Page({
  data: { items: [], reversingId: null, confirmOpen: false, confirmLines: [], reverseForm: { id: null, operator_name: '', remark: '' } },
  onShow() { this.load() },
  onReverseInput(event) {
    this.setData({ [`reverseForm.${event.currentTarget.dataset.field}`]: event.detail.value })
  },
  showReverseForm(event) {
    this.setData({ reverseForm: { id: Number(event.currentTarget.dataset.id), operator_name: '', remark: '' } })
  },
  hideReverseForm() {
    if (!this.data.reversingId) this.setData({ reverseForm: { id: null, operator_name: '', remark: '' }, confirmOpen: false })
  },
  async load() {
    try {
      const items = (await api.productTransactions()).map((item) => ({
        ...item,
        code_text: item.code || '-',
        operator_name_text: item.operator_name || '-',
        remark_text: item.remark || '-',
        can_reverse: ['in', 'out'].includes(item.transaction_type) && !item.reversed_transaction_id,
        is_reversing: this.data.reversingId === item.id
      }))
      this.setData({ items })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  },
  reverse() {
    const { id, operator_name, remark } = this.data.reverseForm
    if (this.data.reversingId) return
    if (!remark.trim()) {
      wx.showToast({ title: '请填写撤销原因', icon: 'none' })
      return
    }
    const item = this.data.items.find((row) => row.id === id)
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '产品', value: item ? item.code_text : String(id) },
        { label: '原流水', value: item ? `${item.transaction_type} / 数量 ${item.quantity}` : String(id) },
        { label: '操作人', value: operator_name || '未填写' },
        { label: '撤销原因', value: remark }
      ]
    })
  },
  cancelConfirm() {
    if (!this.data.reversingId) this.setData({ confirmOpen: false })
  },
  async confirmSubmit() {
    const { id, operator_name, remark } = this.data.reverseForm
    if (this.data.reversingId || !id) return
    const requestTracker = trackerFor(id)
    this.setData({ reversingId: id })
    try {
      const payload = await retryPendingWrite(
        requestTracker,
        { operator_name, remark },
        '撤销产品流水'
      )
      if (!payload) {
        this.setData({ confirmOpen: false })
        return
      }
      await api.reverseProductTransaction(id, payload)
      requestTracker.complete()
      requestTrackers.delete(id)
      this.setData({ confirmOpen: false, reverseForm: { id: null, operator_name: '', remark: '' } })
      wx.showToast({ title: '已撤销', icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '撤销失败', icon: 'none' })
    } finally {
      this.setData({ reversingId: null })
    }
  }
})
