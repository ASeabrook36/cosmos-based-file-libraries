// Deploy at the EXISTING Cosmos account's resource group. Creates only a NEW, disposable
// database holding the three production-path containers, unchanged, for the Phase 4 scale
// probe (scripts/scale_probe.py). Delete the database when the probe is finished.
param accountName string
param databaseName string = 'fli-scale'

resource account 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' existing = {
  name: accountName
}
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-10-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: { id: databaseName }
    options: {}
  }
}
var definitions = [
  loadJsonContent('../cosmos/libraries.container.json')
  loadJsonContent('../cosmos/documents.container.json')
  loadJsonContent('../cosmos/chunks.container.json')
]
resource containers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-10-15' = [for definition in definitions: {
  parent: database
  name: definition.id
  properties: {
    #disable-next-line BCP037
    resource: definition
    options: {}
  }
}]

output containerNames array = [for definition in definitions: definition.id]
