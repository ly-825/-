const { loadAccount } = require('./auth')

function currentOperatorName(wxApi) {
  const account = loadAccount(wxApi)
  return account ? account.display_name : ''
}

module.exports = { currentOperatorName }
