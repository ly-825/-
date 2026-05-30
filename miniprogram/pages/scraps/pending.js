const api = require('../../utils/api')

Page({
  data: { items: [], loading: false },
  onShow() { this.load() },
  async load() {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const items = (await api.pendingScraps()).map((item) => ({
        ...item,
        source_product_code_text: item.source_product_code || '-',
        diameter_text: item.diameter || '-',
        actual_quantity: item.quantity,
        actual_diameter: item.diameter || '',
        confirm_location: '',
        operator_name: '',
        confirming: false
      }))
      this.setData({ items })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
  onInput(event) {
    this.setData({ [`items[${event.currentTarget.dataset.index}].${event.currentTarget.dataset.field}`]: event.detail.value })
  },
  async confirm(event) {
    const index = event.currentTarget.dataset.index
    const item = this.data.items[index]
    if (item.confirming) return
    this.setData({ [`items[${index}].confirming`]: true })
    try {
      await api.confirmScrap(item.id, { actual_quantity: Number(item.actual_quantity), actual_diameter: item.actual_diameter === '' ? null : Number(item.actual_diameter), location: item.confirm_location, operator_name: item.operator_name })
      wx.showToast({ title: '已入库', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '确认失败', icon: 'none' })
    } finally {
      this.setData({ [`items[${index}].confirming`]: false })
    }
  }
})
