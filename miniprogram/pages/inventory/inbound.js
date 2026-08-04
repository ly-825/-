const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const requestTracker = createPendingRequestTracker('product-inbound')

Page({
  data: {
    drawings: [],
    filteredDrawings: [],
    searchKeyword: '',
    selectedDrawingLabel: '尚未选择产品',
    loading: false,
    submitting: false,
    confirmOpen: false,
    confirmLines: [],
    form: { drawing_id: null, quantity: 1, location: '', operator_name: '' }
  },

  onShow() { this.loadDrawings() },

  async loadDrawings() {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const list = await api.confirmedDrawings()
      const drawings = list.map((item) => ({
        ...item,
        product_code_text: item.product_code || '未编号',
        product_category_text: item.product_category || '-',
        product_name_text: item.product_name || '-',
        material_text: item.material || '-',
        thickness_text: item.thickness || item.product_thickness || item.plate_thickness || '-',
        version_text: `A${item.version || 1}`,
        label: `${item.product_code || '未编号'}｜${item.product_category || '-'}｜V${item.version || 1}｜${item.product_name || '-'}｜${item.material || '-'}｜厚度 ${item.thickness || item.product_thickness || item.plate_thickness || '-'}`,
        searchText: [
          item.product_code,
          item.product_category,
          item.product_name,
          item.material,
          item.thickness,
          item.product_thickness,
          item.plate_thickness
        ].filter((value) => value !== undefined && value !== null).join(' ').toLowerCase()
      }))
      this.setData({ drawings })
      this.applySearch()
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  applySearch() {
    const keyword = this.data.searchKeyword.trim().toLowerCase()
    const filteredDrawings = keyword
      ? this.data.drawings.filter((item) => item.searchText.includes(keyword)).slice(0, 20)
      : []
    this.setData({ filteredDrawings })
  },

  onKeyword(event) {
    this.setData({ searchKeyword: event.detail.value })
  },

  onSearch() {
    this.applySearch()
  },

  selectDrawing(event) {
    const drawing = this.data.filteredDrawings[event.currentTarget.dataset.index]
    if (!drawing) return
    this.setData({ 'form.drawing_id': drawing.id, selectedDrawingLabel: drawing.label })
  },

  onInput(event) {
    this.setData({ [`form.${event.currentTarget.dataset.field}`]: event.detail.value })
  },

  submit() {
    if (this.data.submitting) return
    if (!this.data.form.drawing_id) {
      wx.showToast({ title: '请选择产品型号', icon: 'none' })
      return
    }
    this.setData({
      confirmOpen: true,
      confirmLines: [
        { label: '产品', value: this.data.selectedDrawingLabel },
        { label: '数量', value: String(this.data.form.quantity) },
        { label: '库位', value: this.data.form.location || '未填写' },
        { label: '操作人', value: this.data.form.operator_name || '未填写' }
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
      }, '产品入库')
      if (!payload) {
        this.setData({ confirmOpen: false })
        return
      }
      await api.productInbound(payload)
      requestTracker.complete()
      this.setData({ confirmOpen: false })
      wx.showToast({ title: '入库成功', icon: 'success' })
    } catch (error) {
      wx.showToast({ title: error.message || '入库失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
