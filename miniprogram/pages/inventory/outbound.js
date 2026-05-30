const api = require('../../utils/api')

Page({
  data: {
    products: [],
    options: [],
    filteredOptions: [],
    searchKeyword: '',
    selectedLabel: '尚未选择产品',
    loading: false,
    submitting: false,
    form: { drawing_id: null, quantity: 1, location: '', operator_name: '', remark: '' }
  },

  onShow() { this.load() },

  async load() {
    if (this.data.loading) return
    this.setData({ loading: true })
    try {
      const [products, drawings] = await Promise.all([api.products(), api.confirmedDrawings()])
      const drawingMap = {}
      drawings.forEach((drawing) => { if (drawing.product_code) drawingMap[drawing.product_code] = drawing })
      const displayProducts = products.map((item) => ({
        ...item,
        product_code_text: item.product_code || '未编号',
        material_text: item.material || '-',
        thickness_text: item.thickness || '-',
        location_text: item.locations && item.locations.length ? item.locations.join(' / ') : '-'
      }))
      const options = displayProducts.filter((item) => drawingMap[item.product_code]).map((item) => ({
        ...item,
        drawing_id: drawingMap[item.product_code].id,
        product_name_text: drawingMap[item.product_code].product_name || '-',
        label: `${item.product_code_text}｜库存 ${item.quantity}｜库位 ${item.location_text}`,
        searchText: [
          item.product_code,
          drawingMap[item.product_code].product_name,
          item.material,
          item.thickness,
          item.location_text
        ].filter((value) => value !== undefined && value !== null).join(' ').toLowerCase()
      }))
      this.setData({ products: displayProducts, options })
      this.applySearch()
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  applySearch() {
    const keyword = this.data.searchKeyword.trim().toLowerCase()
    const filteredOptions = keyword
      ? this.data.options.filter((item) => item.searchText.includes(keyword)).slice(0, 20)
      : []
    this.setData({ filteredOptions })
  },

  onKeyword(event) {
    this.setData({ searchKeyword: event.detail.value })
  },

  onSearch() {
    this.applySearch()
  },

  selectProduct(event) {
    const option = this.data.filteredOptions[event.currentTarget.dataset.index]
    if (!option) return
    this.setData({ 'form.drawing_id': option.drawing_id, selectedLabel: option.label })
  },

  onInput(event) {
    this.setData({ [`form.${event.currentTarget.dataset.field}`]: event.detail.value })
  },

  async submit() {
    if (this.data.submitting) return
    if (!this.data.form.drawing_id) {
      wx.showToast({ title: '请选择产品', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const clientRequestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`
      await api.productOutbound({ ...this.data.form, quantity: Number(this.data.form.quantity), client_request_id: clientRequestId })
      wx.showToast({ title: '出库成功', icon: 'success' })
      this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '出库失败', icon: 'none' })
    } finally {
      this.setData({ submitting: false })
    }
  }
})
