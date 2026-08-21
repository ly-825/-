const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')
const { currentOperatorName } = require('../../utils/operator')

const requestTrackers = new Map()

function trackerFor(transactionId) {
  if (!requestTrackers.has(transactionId)) {
    requestTrackers.set(
      transactionId,
      createPendingRequestTracker(`scrap-reverse-${transactionId}`)
    )
  }
  return requestTrackers.get(transactionId)
}

Page({
  data: { items: [], operatorName: '', loading: false, error: '', reversingId: null, confirmOpen: false, confirmLines: [], reverseForm: { id: null, remark: '' } },
  onShow() {
    this.setData({ operatorName: currentOperatorName(wx) })
    this.load()
  },
  onReverseInput(event) {
    this.setData({ [`reverseForm.${event.currentTarget.dataset.field}`]: event.detail.value })
  },
  showReverseForm(event) {
    this.setData({ reverseForm: { id: event.currentTarget.dataset.id, remark: '' } })
  },
  hideReverseForm() {
    if (!this.data.reversingId) this.setData({ reverseForm: { id: null, remark: '' }, confirmOpen: false })
  },
  async load() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      const items = (await api.scrapTransactions()).map((item) => ({
        ...item,
        material_text: item.material || '-',
        usable_size_text: item.usable_size || '-',
        location_text: item.location || '-',
        operator_name_text: item.operator_name || '-',
        can_reverse: ['in', 'out'].includes(item.transaction_type) && !item.reversed_transaction_id,
        is_reversing: this.data.reversingId === item.id,
        show_reverse_form: this.data.reverseForm.id === item.id
      }))
      this.setData({ items })
    } catch (error) {
      this.setData({ error: error.message || '余料流水加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
  reverse() {
    const { id, remark } = this.data.reverseForm
    if (this.data.reversingId) return
    if (!remark.trim()) {
      wx.showToast({ title: '请填写撤销原因', icon: 'none' })
      return
    }
    const item = this.data.items.find((row) => String(row.id) === String(id))
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '余料', value: item ? `${item.material_text} / ${item.usable_size_text}` : String(id) },
        { label: '原流水', value: item ? `${item.transaction_type} / 数量 ${item.quantity}` : String(id) },
        { label: '操作人', value: this.data.operatorName || '当前账号' },
        { label: '撤销原因', value: remark }
      ]
    })
  },
  cancelConfirm() {
    if (!this.data.reversingId) this.setData({ confirmOpen: false })
  },
  async confirmSubmit() {
    const { id, remark } = this.data.reverseForm
    if (this.data.reversingId || !id) return
    const requestTracker = trackerFor(id)
    this.setData({ reversingId: id })
    try {
      const payload = await retryPendingWrite(
        requestTracker,
        { remark },
        '撤销余料流水'
      )
      if (!payload) {
        this.setData({ confirmOpen: false })
        return
      }
      await api.reverseScrapTransaction(id, payload)
      requestTracker.complete()
      requestTrackers.delete(id)
      this.setData({ confirmOpen: false, reverseForm: { id: null, remark: '' } })
      wx.showToast({ title: '已撤销', icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '撤销失败', icon: 'none' })
    } finally {
      this.setData({ reversingId: null })
    }
  }
})
