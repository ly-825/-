const api = require('../../utils/api')

Page({
  data: {
    filters: { q: '', material: '', thickness: '' },
    items: [],
    loading: false,
    error: ''
  },

  onShow() { this.load() },

  onFilter(event) {
    this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value })
  },

  async load() {
    if (this.data.loading) return
    this.setData({ loading: true, error: '' })
    try {
      const items = (await api.products(this.data.filters)).map((item) => ({
        ...item,
        material_text: item.material || '-',
        thickness_text: item.thickness || '-',
        location_text: item.locations && item.locations.length ? item.locations.join(' / ') : '-'
      }))
      this.setData({ items })
    } catch (error) {
      this.setData({ error: error.message || '库存加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
