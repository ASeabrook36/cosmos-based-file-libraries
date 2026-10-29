// Deploy at the EXISTING Cosmos account's resource group. Creates only a NEW lab database.
param accountName string
param databaseName string
param workflowPrincipalId string
@allowed(['serverless', 'provisioned'])
param capacityMode string = 'serverless'
param maxThroughput int = 4000

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
  loadJsonContent('../cosmos/indexer-lab.container.json')
]
resource containers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-10-15' = [for definition in definitions: {
  parent: database
  name: definition.id
  properties: {
    #disable-next-line BCP037
    resource: definition
    options: capacityMode == 'serverless' ? {} : {autoscaleSettings: {maxThroughput: maxThroughput}}
  }
}]
resource dataAccess 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-10-15' = {
  parent: account
  name: guid(database.id, workflowPrincipalId, 'indexer-lab-data-contributor')
  properties: {
    roleDefinitionId: '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: workflowPrincipalId
    scope: '${account.id}/dbs/${databaseName}'
  }
}

output databaseResourceId string = database.id
output dataRoleAssignmentId string = dataAccess.id
