const api = require('../../utils/api')

const confirmFields = [
  'product_code',
  'product_name',
  'product_category',
  'remark',
  'material',
  'max_outer_diameter',
  'min_inner_diameter',
  'expected_scrap_size',
  'product_thickness',
  'plate_thickness',
  'teeth_count',
  'module',
  'pressure_angle',
  'profile_shift_coefficient',
  'span_teeth_count',
  'common_normal_length',
  'pin_diameter',
  'pin_span'
]

const numericFields = [
  'max_outer_diameter',
  'min_inner_diameter',
  'product_thickness',
  'plate_thickness',
  'teeth_count',
  'module',
  'pressure_angle',
  'profile_shift_coefficient',
  'span_teeth_count',
  'common_normal_length',
  'pin_diameter',
  'pin_span'
]

Page({
  data: { id: null, form: {}, loading: false, confirming: false, rerunning: false, deleting: false },

  onLoad(options) {
    this.setData({ id: options.id })
    this.load()
  },

  async load() {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const drawing = await api.drawingDetail(this.data.id)
      this.setData({ form: drawing })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  onInput(event) {
    const field = event.currentTarget.dataset.field
    this.setData({ [`form.${field}`]: event.detail.value })
  },

  buildPayload() {
    const payload = {}
    confirmFields.forEach((field) => {
      payload[field] = this.data.form[field] === undefined ? null : this.data.form[field]
    })
    numericFields.forEach((field) => {
      payload[field] = payload[field] === '' || payload[field] == null ? null : Number(payload[field])
    })
    return payload
  },

  async confirm() {
    if (this.data.confirming) return
    this.setData({ confirming: true })
    try {
      await api.confirmDrawing(this.data.id, this.buildPayload())
      wx.showToast({ title: '已确认', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '确认失败', icon: 'none' })
    } finally {
      this.setData({ confirming: false })
    }
  },

  async rerun() {
    if (this.data.rerunning) return
    this.setData({ rerunning: true })
    try {
      await api.rerunDrawing(this.data.id)
      wx.showToast({ title: '已重新识别', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '识别失败', icon: 'none' })
    } finally {
      this.setData({ rerunning: false })
    }
  },

  deleteDrawing() {
    if (this.data.deleting) return
    wx.showModal({
      title: '删除图纸',
      content: '确定删除这张图纸吗？删除后如需更新可以重新上传。已产生的库存记录不会被删除。',
      confirmText: '删除',
      confirmColor: '#d92d20',
      success: async (res) => {
        if (!res.confirm) return
        this.setData({ deleting: true })
        try {
          await api.deleteDrawing(this.data.id)
          wx.showToast({ title: '已删除', icon: 'success' })
          setTimeout(() => {
            wx.navigateBack()
          }, 500)
        } catch (error) {
          wx.showToast({ title: error.message || '删除失败', icon: 'none' })
        } finally {
          this.setData({ deleting: false })
        }
      }
    })
  }
})
