const api = require('../../utils/api')

Page({
  data: { status: '', title: '图纸列表', q: '', product_thickness: '', plate_thickness: '', items: [] },

  onLoad(options) {
    const title = options.status === 'pending' ? '待确认图纸' : options.status === 'confirmed' ? '已确认图纸' : '图纸列表'
    this.setData({ status: options.status || '', title })
    this.load()
  },

  onSearchInput(event) {
    this.setData({ q: event.detail.value })
  },

  onFilterInput(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [field]: event.detail.value })
  },

  async load() {
    try {
      const items = (await api.drawings({
        status: this.data.status,
        q: this.data.q,
        product_thickness: this.data.product_thickness,
        plate_thickness: this.data.plate_thickness
      })).map((item) => ({
        ...item,
        product_code_text: item.product_code || '未识别编号',
        product_category_text: item.product_category || '-',
        product_name_text: item.product_name || '-',
        remark_text: item.remark || '',
        material_text: item.material || '-',
        product_thickness_text: item.product_thickness || '-',
        plate_thickness_text: item.plate_thickness || '-',
        max_outer_diameter_text: item.max_outer_diameter || '-',
        confirmed_text: item.confirmed ? '已确认' : '待确认',
        version_text: `A${item.version || 1}`,
        active_text: item.is_active ? '当前' : '历史'
      }))
      this.setData({ items })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  },

  goDetail(event) {
    wx.navigateTo({ url: `/pages/drawings/detail?id=${event.currentTarget.dataset.id}` })
  }
})
