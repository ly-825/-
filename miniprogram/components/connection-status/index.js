Component({
  properties: {
    state: { type: String, value: 'unknown' },
    baseUrl: { type: String, value: '' }
  },
  methods: {
    retry() {
      this.triggerEvent('retry')
    },
    configure() {
      this.triggerEvent('configure')
    }
  }
})
