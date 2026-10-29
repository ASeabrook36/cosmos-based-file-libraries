// Retain both member IDs, including deleted libraries, until no workflow can still write.
param jobName string
param location string = resourceGroup().location
param managedEnvironmentId string
param managedIdentityId string
param managedIdentityClientId string
param image string
param registryServer string
param blobAccountUrl string
param servicebusNamespace string
param servicebusQueue string = 'ingest'
@minLength(1)
param libraryIds array

resource reconciliation 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${managedIdentityId}': {} } }
  properties: {
    environmentId: managedEnvironmentId
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 120
      replicaRetryLimit: 1
      registries: [{ server: registryServer, identity: managedIdentityId }]
      scheduleTriggerConfig: { cronExpression: '*/2 * * * *', parallelism: 1, replicaCompletionCount: 1 }
    }
    template: {
      containers: [{
        name: 'reconcile'
        image: image
        command: ['python', '-m', 'fli.reconcile']
        args: concat(['--discover-blob-scopes'], flatten(map(libraryIds, id => ['--library-id', id])))
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'ENVIRONMENT', value: 'demo' }
          { name: 'AUTH_MODE', value: 'entra' }
          { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
          { name: 'BLOB_ACCOUNT_URL', value: blobAccountUrl }
          { name: 'SERVICEBUS_NAMESPACE', value: servicebusNamespace }
          { name: 'SERVICEBUS_QUEUE', value: servicebusQueue }
        ]
      }]
    }
  }
}
