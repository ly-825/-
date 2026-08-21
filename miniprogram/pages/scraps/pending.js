const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')
const { currentOperatorName } = require('../../utils/operator')

const requestTrackers = new Map()

function trackerFor(inventoryId) {
  if (!requestTrackers.has(inventoryId)) {
    requestTrackers.set(
      inventoryId,
      createPendingRequestTracker(`scrap-confirm-${inventoryId}`)
    )
  }
  return requestTrackers.get(inventoryId)
}

Page({
  data: {
    items: [],
    loading: false,
    error: '',
    submitting: false,
    confirmOpen: false,
    confirmLines: [],
    confirmingIndex: null,
    operatorName: ''
  },
  onShow() {
    this.setData({ operatorName: currentOperatorName(wx) })
    this.load()
  },
  async load() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      const items = (await api.pendingScraps()).map((item) => ({
        ...item,
        source_product_code_text: item.source_product_code || '-',
        diameter_text: item.diameter || '-',
        actual_quantity: item.quantity,
        actual_diameter: item.diameter || '',
        confirm_location: '',
        confirming: false
      }))
      this.setData({ items })
    } catch (error) {
      this.setData({ error: error.message || '待入库余料加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
  onInput(event) {
    this.setData({ [`items[${event.currentTarget.dataset.index}].${event.currentTarget.dataset.field}`]: event.detail.value })
  },
  confirm(event) {
    const index = event.currentTarget.dataset.index
    const item = this.data.items[index]
    if (!item || item.confirming || this.data.submitting) return
    if (!String(item.confirm_location || '').trim()) {
      wx.showToast({ title: '请填写库位', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmingIndex: index,
      confirmLines: [
        { label: '来源产品', value: item.source_product_code_text },
        { label: '材质/厚度', value: `${item.material} / ${item.thickness}` },
        { label: '实际数量', value: String(item.actual_quantity) },
        { label: '实际直径', value: item.actual_diameter === '' ? '沿用理论值' : String(item.actual_diameter) },
        { label: '库位', value: item.confirm_location },
        { label: '确认人', value: this.data.operatorName || '当前账号' }
      ]
    })
  },
  cancelConfirm() {
    if (!this.data.submitting) this.setData({ confirmOpen: false, confirmingIndex: null })
  },
  async confirmSubmit() {
    const index = this.data.confirmingIndex
    const item = this.data.items[index]
    if (!item || this.data.submitting) return
    this.setData({ submitting: true, [`items[${index}].confirming`]: true })
    const requestTracker = trackerFor(item.id)
    try {
      const payload = await retryPendingWrite(requestTracker, {
        actual_quantity: Number(item.actual_quantity),
        actual_diameter: item.actual_diameter === '' ? null : Number(item.actual_diameter),
        location: item.confirm_location
      }, '余料确认入库')
      if (!payload) {
        this.setData({ confirmOpen: false, confirmingIndex: null })
        return
      }
      await api.confirmScrap(item.id, payload)
      requestTracker.complete()
      requestTrackers.delete(item.id)
      this.setData({ confirmOpen: false, confirmingIndex: null })
      wx.showToast({ title: '已入库', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '确认失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false, [`items[${index}].confirming`]: false })
    }
  }
})
