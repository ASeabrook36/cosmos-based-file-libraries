// ---------------------------------------------------------------------------
// cosmos-account.bicep — one organization's Cosmos DB account and database.
//
// Adapted from account definitions that deployed and served vector and hybrid
// search successfully. This exact file has not been deployed yet: Phase 0
// deploys it and the canary proves it (docs/build-plan.md).
// ---------------------------------------------------------------------------

@description('Cosmos DB account name (globally unique, lowercase).')
param accountName string

@description('Azure region.')
param location string = resourceGroup().location

@description('serverless for the demo; provisioned for production (docs/spec.md §11).')
@allowed([
  'serverless'
  'provisioned'
])
param capacityMode string = 'serverless'

@description('Provisioned only: cap on total configured throughput across the account, in RU/s.')
param totalThroughputLimit int = 10000

@description('Database name.')
param databaseName string = 'fli'

resource account 'Microsoft.DocumentDB/databaseAccounts@2025-10-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: union(
    {
      databaseAccountOfferType: 'Standard'
      consistencyPolicy: {
        defaultConsistencyLevel: 'Session'
      }
      locations: [
        {
          locationName: location
          failoverPriority: 0
          isZoneRedundant: false
        }
      ]
      // EnableNoSQLVectorSearch is required for vector policies. Full-text and
      // hybrid search worked with no further capability on the source accounts.
      // EnableServerless is a creation-time capability: it cannot be added or
      // removed later. Conversion runs serverless -> provisioned only, one way.
      capabilities: union(
        [
          {
            name: 'EnableNoSQLVectorSearch'
          }
        ],
        capacityMode == 'serverless'
          ? [
              {
                name: 'EnableServerless'
              }
            ]
          : []
      )
      publicNetworkAccess: 'Enabled'
      enableFreeTier: false
      // Stated because the service sets it to true on its own. Left unstated,
      // every what-if shows a spurious `true => false` change on the account.
      enableAutomaticFailover: true
      // Data-plane access by Entra role assignment only; no account keys.
      disableLocalAuth: true
      backupPolicy: {
        type: 'Continuous'
        continuousModeProperties: {
          tier: 'Continuous7Days'
        }
      }
    },
    // totalThroughputLimit applies to provisioned accounts only; a serverless
    // account configures no throughput.
    capacityMode == 'serverless'
      ? {}
      : {
          capacity: {
            totalThroughputLimit: totalThroughputLimit
          }
        }
  )
}

// No database-level throughput: vector containers cannot use shared
// throughput, and a container created under shared throughput cannot be
// converted to dedicated in place.
resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-10-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
