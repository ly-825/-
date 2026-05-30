const api = require('../../utils/api')

Page({
  data: { filters: { material: '', thickness: '', required_diameter: '', location: '' }, items: [], selectedLabel: '选择余料规格', loading: false, submitting: false, form: { scrap_group_key: '', quantity: 1, operator_name: '', remark: '' } },
  onShow() { this.load() },
  onFilter(event) { this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  onInput(event) { this.setData({ [`form.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  async load() {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const items = (await api.scraps(this.data.filters)).map((item) => ({
        ...item,
        label: `${item.material}｜厚度 ${item.thickness}｜${item.usable_size}｜库位 ${item.location}｜数量 ${item.quantity}`
      }))
      this.setData({ items })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  onPick(event) {
    const item = this.data.items[Number(event.detail.value)]
    this.setData({ 'form.scrap_group_key': item.group_key, selectedLabel: item.label })
  },
  async submit() {
    if (this.data.submitting) return
    if (!this.data.form.scrap_group_key) {
      wx.showToast({ title: '请选择余料规格', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const clientRequestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`
      await api.scrapOutbound({ ...this.data.form, quantity: Number(this.data.form.quantity), client_request_id: clientRequestId })
      wx.showToast({ title: '出库成功', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '出库失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
