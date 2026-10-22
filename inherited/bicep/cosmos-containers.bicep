// ---------------------------------------------------------------------------
// cosmos-containers.bicep — the three containers, from cosmos/*.json.
//
// The container bodies are loaded, never retyped, so cosmos/*.json stays the
// single definition (CLAUDE.md rule 9). Adapted from container definitions
// that deployed successfully; this exact file has not been deployed yet.
//
// Lessons carried in the JSON, worth knowing before editing it:
//   - Never list /id/? in includedPaths. Cosmos accepts the create, then fails
//     it asynchronously; the container is never created and nothing reports
//     it. id is indexed regardless.
//   - With the root excluded, every partition-key path and the shard-key path
//     must be listed, or queries filtering on them scan.
//   - Vector settings (dimensions, dataType, distance, index type, quantizer,
//     indexingSearchListSize, vectorIndexShardKey) are permanent. A change is a
//     new container and a rebuild.
//   - A deployment that succeeds is not proof the vector index works. Run
//     scripts/canary.py against every new container before using it.
// ---------------------------------------------------------------------------

@description('Existing Cosmos DB account name.')
param accountName string

@description('Existing database name.')
param databaseName string = 'fli'

@description('serverless: no throughput settings. provisioned: dedicated autoscale per container.')
@allowed([
  'serverless'
  'provisioned'
])
param capacityMode string = 'serverless'

@description('Provisioned only: autoscale maximum RU/s for chunks (docs/spec.md §11 starting point).')
param chunksMaxThroughput int = 4000

@description('Provisioned only: autoscale maximum RU/s for libraries and documents.')
param metadataMaxThroughput int = 1000

var definitions = [
  {
    body: loadJsonContent('../../cosmos/libraries.container.json')
    maxThroughput: metadataMaxThroughput
  }
  {
    body: loadJsonContent('../../cosmos/documents.container.json')
    maxThroughput: metadataMaxThroughput
  }
  {
    body: loadJsonContent('../../cosmos/chunks.container.json')
    maxThroughput: chunksMaxThroughput
  }
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
  name: definition.body.id
  properties: {
    resource: definition.body
    // Supplying autoscaleSettings against a serverless account is rejected.
    options: capacityMode == 'serverless'
      ? {}
      : {
          autoscaleSettings: {
            maxThroughput: definition.maxThroughput
          }
        }
  }
}]

output containerNames array = [for (definition, i) in definitions: containers[i].name]
