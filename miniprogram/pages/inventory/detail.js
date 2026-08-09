const api = require('../../utils/api')

function display(value) {
  return value === null || value === undefined || value === '' ? '-' : value
}

function displayTime(value) {
  const text = String(value || '')
  return text ? text.replace('T', ' ').slice(0, 19) : '-'
}

function transactionLabel(value) {
  return {
    in: '入库',
    out: '出库',
    confirm: '确认入库'
  }[value] || value || '-'
}

Page({
  data: {
    product: {},
    totalQuantity: 0,
    batches: [],
    transactions: [],
    loading: false,
    error: ''
  },

  onShow() {
    this.load()
  },

  async load() {
    const product = wx.getStorageSync('product-detail') || {}
    if (!product.product_code) {
      this.setData({ product, error: '缺少产品型号，请返回库存列表重新选择' })
      return
    }
    this.setData({ product, loading: true, error: '' })
    try {
      const [batchRows, transactionRows] = await Promise.all([
        api.productBatches(product.product_code),
        api.productTransactions({ product_code: product.product_code })
      ])
      const batches = batchRows.map((item) => ({
        ...item,
        material_text: display(item.material),
        paper_material_text: display(item.paper_material),
        product_thickness_text: display(item.product_thickness),
        plate_thickness_text: display(item.plate_thickness),
        location_text: display(item.location),
        status_text: item.status === 'available' ? '可用' : (item.status === 'used' ? '已用完' : display(item.status)),
        created_at_text: displayTime(item.created_at),
        updated_at_text: displayTime(item.updated_at)
      }))
      const transactions = transactionRows.map((item) => ({
        ...item,
        transaction_type_text: transactionLabel(item.transaction_type),
        customer_text: display(item.customer_name),
        operator_text: display(item.operator_name),
        remark_text: display(item.remark),
        created_at_text: displayTime(item.created_at)
      }))
      this.setData({
        batches,
        transactions,
        totalQuantity: batches.reduce((sum, item) => sum + Number(item.quantity || 0), 0)
      })
    } catch (error) {
      this.setData({ error: error.message || '成品批次加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
