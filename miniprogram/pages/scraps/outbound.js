const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const requestTracker = createPendingRequestTracker('scrap-outbound')

Page({
  data: { filters: { material: '', thickness: '', required_diameter: '', location: '' }, items: [], selectedLabel: '选择余料规格', loading: false, error: '', submitting: false, confirmOpen: false, confirmLines: [], form: { scrap_group_key: '', quantity: 1, operator_name: '', remark: '' } },
  onShow() { this.load() },
  onFilter(event) { this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  onInput(event) { this.setData({ [`form.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  async load() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      const items = (await api.scraps(this.data.filters)).map((item) => ({
        ...item,
        label: `${item.material}｜厚度 ${item.thickness}｜${item.usable_size}｜库位 ${item.location}｜数量 ${item.quantity}`
      }))
      this.setData({ items })
    } catch (error) {
      this.setData({ error: error.message || '可出库余料加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  },
  onPick(event) {
    const item = this.data.items[Number(event.detail.value)]
    this.setData({ 'form.scrap_group_key': item.group_key, selectedLabel: item.label })
  },
  submit() {
    if (this.data.submitting) return
    if (!this.data.form.scrap_group_key) {
      wx.showToast({ title: '请选择余料规格', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '余料规格', value: this.data.selectedLabel },
        { label: '数量', value: String(this.data.form.quantity) },
        { label: '操作人', value: this.data.form.operator_name || '未填写' },
        { label: '备注', value: this.data.form.remark || '未填写' }
      ]
    })
  },
  cancelConfirm() {
    if (!this.data.submitting) this.setData({ confirmOpen: false })
  },
  async confirmSubmit() {
    if (this.data.submitting) return
    this.setData({ submitting: true })
    try {
      const payload = await retryPendingWrite(requestTracker, {
        ...this.data.form,
        quantity: Number(this.data.form.quantity)
      }, '余料出库')
      if (!payload) {
        this.setData({ confirmOpen: false })
        return
      }
      await api.scrapOutbound(payload)
      requestTracker.complete()
      this.setData({ confirmOpen: false })
      wx.showToast({ title: '出库成功', icon: 'success' })
      await this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '出库失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
