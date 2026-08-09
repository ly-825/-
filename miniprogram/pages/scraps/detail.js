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
    group: {},
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
    const group = wx.getStorageSync('scrap-detail') || {}
    if (!group.group_key) {
      this.setData({ group, error: '缺少余料规格，请返回余料库存重新选择' })
      return
    }
    this.setData({ group, loading: true, error: '' })
    try {
      const result = await api.scrapBatches(group.group_key)
      const batches = (result.batches || []).map((item) => ({
        ...item,
        source_product_text: display(item.source_product_code),
        source_drawing_text: display(item.source_drawing_label),
        location_text: display(item.location),
        usable_size_text: display(item.usable_size),
        theoretical_size_text: display(item.theoretical_size),
        actual_size_text: display(item.actual_size),
        operator_text: display(item.operator_name),
        registered_at_text: displayTime(item.registered_at),
        status_text: item.status === 'available' ? '可用' : display(item.status)
      }))
      const transactions = (result.transactions || []).map((item) => ({
        ...item,
        transaction_type_text: transactionLabel(item.transaction_type),
        location_text: display(item.location),
        customer_text: display(item.customer_name),
        operator_text: display(item.operator_name),
        remark_text: display(item.remark),
        created_at_text: displayTime(item.created_at)
      }))
      this.setData({
        totalQuantity: Number(result.total_quantity || 0),
        batches,
        transactions
      })
    } catch (error) {
      this.setData({ error: error.message || '余料批次加载失败' })
    } finally {
      this.setData({ loading: false })
    }
  }
})
