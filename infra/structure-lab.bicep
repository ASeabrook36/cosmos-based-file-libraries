// Opt-in experiment. The normal deployment does not create or use these indexes.
param accountName string
param databaseName string = 'fli'
@allowed(['serverless', 'provisioned'])
param capacityMode string = 'serverless'
param maxThroughput int = 4000

var definitions = [
  loadJsonContent('../cosmos/lab-shared.container.json')
  loadJsonContent('../cosmos/lab-passages.container.json')
  loadJsonContent('../cosmos/lab-summaries.container.json')
]
resource account 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' existing = {
  name: accountName
}
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-10-15' existing = {
  parent: account
  name: databaseName
}
resource containers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-10-15' = [for definition in definitions: {
  parent: database
  name: definition.id
  properties: {
    #disable-next-line BCP037
    resource: definition
    options: capacityMode == 'serverless' ? {} : {autoscaleSettings: {maxThroughput: maxThroughput}}
  }
}]
output containerNames array = [for definition in definitions: definition.id]
