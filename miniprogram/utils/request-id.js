function createRequestId(nowFn = Date.now, randomFn = Math.random) {
  const randomPart = Math.floor(randomFn() * 0x100000000)
    .toString(16)
    .padStart(8, '0')
  return `mobile-${nowFn()}-${randomPart}`
}

module.exports = { createRequestId }
