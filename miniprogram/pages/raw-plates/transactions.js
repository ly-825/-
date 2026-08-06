const api = require('../../utils/api')
const { createPendingRequestTracker } = require('../../utils/request-id')
const { retryPendingWrite } = require('../../utils/pending-write')

const tracker = createPendingRequestTracker('raw-plate-reverse')

Page({
  data: {
    items: [],
    target: null,
    loading: false,
    error: '',
    confirmOpen: false,
    confirmLines: [],
    submitting: false,
  },

  onShow() {
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      this.setData({ items: await api.rawPlateTransactions() })
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ loading: false })
    }
  },

  reverse(event) {
    const transaction = this.data.items[event.currentTarget.dataset.index]
    this.setData({
      target: transaction,
      confirmOpen: true,
      confirmLines: [
        { label: '流水', value: String(transaction.id) },
        { label: '批次', value: transaction.batch.material_code || '-' },
        { label: '库存变化', value: `${transaction.before_quantity} → ${transaction.after_quantity}` },
      ],
    })
  },

  cancelConfirm() {
    this.setData({ confirmOpen: false })
  },

  async confirmSubmit() {
    this.setData({ submitting: true })
    try {
      const pending = await retryPendingWrite(
        tracker,
        {
          transaction_id: this.data.target.id,
          operator_name: '',
          remark: '小程序撤销',
        },
        '撤销钢板流水',
      )
      if (!pending) return
      const transactionId = pending.transaction_id
      await api.reverseRawPlateTransaction(transactionId, pending)
      tracker.complete()
      this.setData({ confirmOpen: false })
      this.load()
    } catch (error) {
      this.setData({ error: error.message })
    } finally {
      this.setData({ submitting: false })
    }
  },
})
