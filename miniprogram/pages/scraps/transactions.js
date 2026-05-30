const api = require('../../utils/api')

Page({
  data: { items: [], reversingId: null, reverseForm: { id: null, operator_name: '', remark: '' } },
  onShow() { this.load() },
  onReverseInput(event) {
    this.setData({ [`reverseForm.${event.currentTarget.dataset.field}`]: event.detail.value })
  },
  showReverseForm(event) {
    this.setData({ reverseForm: { id: event.currentTarget.dataset.id, operator_name: '', remark: '' } })
  },
  hideReverseForm() {
    this.setData({ reverseForm: { id: null, operator_name: '', remark: '' } })
  },
  async load() {
    try {
      const items = (await api.scrapTransactions()).map((item) => ({
        ...item,
        material_text: item.material || '-',
        usable_size_text: item.usable_size || '-',
        location_text: item.location || '-',
        operator_name_text: item.operator_name || '-',
        can_reverse: ['in', 'out'].includes(item.transaction_type) && !item.reversed_transaction_id,
        is_reversing: this.data.reversingId === item.id,
        show_reverse_form: this.data.reverseForm.id === item.id
      }))
      this.setData({ items })
    } catch (error) {
      wx.showToast({ title: error.message || '加载失败', icon: 'none' })
    }
  },
  reverse() {
    const { id, operator_name, remark } = this.data.reverseForm
    if (this.data.reversingId) return
    if (!remark.trim()) {
      wx.showToast({ title: '请填写撤销原因', icon: 'none' })
      return
    }
    wx.showModal({
      title: '撤销流水',
      content: '确定撤销这条余料流水吗？系统会生成一条反向流水，不会删除原记录。',
      confirmText: '撤销',
      success: async (res) => {
        if (!res.confirm) return
        this.setData({ reversingId: id })
        try {
          await api.reverseScrapTransaction(id, { operator_name, remark })
          wx.showToast({ title: '已撤销', icon: 'success' })
          this.hideReverseForm()
          this.load()
        } catch (error) {
          wx.showToast({ title: error.message || '撤销失败', icon: 'none' })
        } finally {
          this.setData({ reversingId: null })
        }
      }
    })
  }
})
