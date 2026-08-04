Component({
  properties: {
    open: { type: Boolean, value: false },
    title: { type: String, value: '确认操作' },
    lines: { type: Array, value: [] },
    danger: { type: Boolean, value: false },
    submitting: { type: Boolean, value: false }
  },
  methods: {
    cancel() {
      if (!this.data.submitting) this.triggerEvent('cancel')
    },
    confirm() {
      if (!this.data.submitting) this.triggerEvent('confirm')
    }
  }
})
