const api = require('../../utils/api')

Page({
  data: {
    baseUrl: '', loading: false, matching: false, error: '', drawings: [],
    selectedDrawingId: null, selectedDrawingLabel: '尚未选择图纸', quantity: 1,
    filters: { q: '', material: '', thickness: '', outer_diameter: '', inner_diameter: '', teeth_count: '' },
    result: null
  },

  onShow() {
    if (getApp().globalData.connectionState !== 'connected') {
      wx.reLaunch({ url: '/pages/connection/index' })
      return
    }
    this.setData({ baseUrl: getApp().globalData.baseUrl })
    this.loadDrawings()
  },

  configure() { wx.reLaunch({ url: '/pages/connection/index' }) },
  onFilter(event) { this.setData({ [`filters.${event.currentTarget.dataset.field}`]: event.detail.value }) },
  onQuantity(event) { this.setData({ quantity: event.detail.value }) },

  async loadDrawings() {
    this.setData({ loading: true, error: '', result: null })
    try {
      const drawings = await api.planDrawings(this.data.filters)
      this.setData({ drawings })
      if (drawings.length === 1) this.selectDrawingByItem(drawings[0])
    } catch (error) {
      this.setData({ error: error.message || '图纸查询失败' })
    } finally {
      this.setData({ loading: false })
    }
  },

  selectDrawingByItem(item) {
    this.setData({
      selectedDrawingId: item.id,
      selectedDrawingLabel: `${item.product_code || '未编号'}｜${item.product_name || '-'}｜${item.material || '-'}｜厚度 ${item.thickness || '-'}`,
      result: null
    })
  },

  selectDrawing(event) {
    const item = this.data.drawings.find((drawing) => drawing.id === Number(event.currentTarget.dataset.id))
    if (item) this.selectDrawingByItem(item)
  },

  async matchMaterials() {
    const quantity = Number(this.data.quantity)
    if (!this.data.selectedDrawingId || !Number.isInteger(quantity) || quantity <= 0) {
      wx.showToast({ title: '请选择图纸并填写正整数计划数量', icon: 'none' })
      return
    }
    this.setData({ matching: true, error: '' })
    try {
      const result = await api.planMatch({ drawing_id: this.data.selectedDrawingId, quantity })
      this.setData({ result })
    } catch (error) {
      this.setData({ error: error.message || '材料匹配失败' })
    } finally {
      this.setData({ matching: false })
    }
  }
})
