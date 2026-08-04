Component({
  properties: {
    state: { type: String, value: 'empty' },
    title: { type: String, value: '' },
    description: { type: String, value: '' }
  },
  methods: {
    retry() {
      this.triggerEvent('retry')
    }
  }
})
